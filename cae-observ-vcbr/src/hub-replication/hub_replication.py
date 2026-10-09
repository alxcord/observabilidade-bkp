"""Replica o hub operacional (Azure SQL) para Delta.

Existe porque o Azure SQL é dimensionado para o caminho de alerta — a
carga do hub é escrita transacional mais o DirectQuery do Power BI, não
histórico de anos — e a purga (documento §10) precisa de um destino
confirmado antes de apagar. Sem este job, `tbl_ctl_replication.replicated_watermark`
nunca avança, e a purga trava para sempre: as duas condições dela são idade
**e** replicação, nunca só idade.

  IMPORTANTE — por que isto roda em Container Apps, não em Databricks:
  a primeira versão deste job era um job Databricks lendo o Azure SQL
  direto por JDBC. Um workspace **sem VNet injection** — o caso comum —
  roda o compute numa VNet gerenciada pela Microsoft, sem rota possível
  até o Private Endpoint do banco: não é configuração faltando, é
  impossibilidade estrutural. É o mesmo problema que já motivava os jobs
  de Data Quality e dos pilares nunca escreverem direto no hub (documento
  §5.6) — só que ali na direção contrária.

  A saída usa a mesma assimetria que já sustenta `sync_execution_expectations.py`
  e `hub_publisher.py` (que faz o caminho inverso, do hub para o
  Databricks): as APIs REST do Databricks (Files API, Statement Execution
  API) são HTTPS públicas, alcançáveis de qualquer lugar, **independente**
  de VNet injection no workspace — porque quem inicia a conexão é sempre o
  lado de fora, nunca o compute do Databricks. Este job lê o Azure SQL por
  pyodbc (mesmo padrão do coletor e do sync-expectations, já resolvido),
  grava o lote em CSV, sobe para um Volume do Unity Catalog pela Files
  API, e executa `COPY INTO` pela Statement Execution API contra um **SQL
  Warehouse serverless com auto-stop curto**.

  Cadência: de hora em hora (`15 * * * *`, quinze minutos depois de
  `hub-publisher`, para que o incidente aberto num ciclo já esteja
  confirmado no hub a tempo de este job exportá-lo) — não mais uma vez por
  dia. Isso multiplica os wake-ups do warehouse serverless por ~24; com
  volume pequeno por lote, o custo por wake-up (não por linha) passa a
  dominar. **Não medido contra o warehouse real** — validar antes de
  assumir que continua "insensível à cadência" como o README principal da
  Observabilidade descreve para o coletor.

Fluxo, por tabela (`constants.SOURCE_TABLES`):

    Azure SQL (pyodbc)  -->  CSV local  -->  Files API (Volume)  -->  COPY INTO (raw)  -->  COPY INTO (cleansed)

O novo watermark é o **mínimo** dos maiores timestamps lidos em cada
tabela nesta execução — nunca o máximo. Se uma tabela não trouxe nada
porque a leitura falhou no meio, avançar o watermark pelas outras duas
marcaria como replicado um período que a terceira não confirmou, e a purga
apagaria dado que nunca chegou ao Delta.

**O nome do arquivo de staging é determinístico por tabela e por
watermark anterior, nunca aleatório.** Um retry após falha parcial (upload
subiu, `COPY INTO` falhou, ou o processo caiu no meio) relê exatamente as
mesmas linhas do Azure SQL, porque o watermark só avança depois de tudo
confirmado. Se o nome do arquivo fosse aleatório a cada tentativa, o
`COPY INTO` — que deduplica por nome de arquivo já processado — não teria
como reconhecer que aquele lote já tinha sido carregado, e um retry
duplicaria linhas.

Configuração por variável de ambiente (mesmo padrão de
`sync_execution_expectations.py`, este é um Container Apps Job — quem
provisiona os valores é o Bicep, não uma chamada manual com flags):

    SQL_SERVER_FQDN          servidor do hub, ex.: sqlsrv-observ-vcbr.database.windows.net
    SQL_DATABASE             nome do banco
    DATABRICKS_HOST          URL do workspace, ex.: https://<workspace>.azuredatabricks.net
    DATABRICKS_WAREHOUSE_ID  SQL Warehouse serverless que recebe o COPY INTO —
                              auto-stop curto: o job só precisa dele ligado
                              pelos minutos da carga, uma vez por dia
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import struct
import time
from dataclasses import dataclass
from datetime import datetime

import httpx
import pyodbc
from azure.identity import DefaultAzureCredential

import constants as c

logger = logging.getLogger("hub-replication")


# ---------------------------------------------------------------------------
# Controle de watermark e leitura incremental — pyodbc, Managed Identity.
# Mesmo padrão do coletor e do sync-expectations: sem usuário e senha em
# lugar nenhum, o token entra pelo atributo do driver ODBC.
# ---------------------------------------------------------------------------

def connect_sql(credential: DefaultAzureCredential) -> pyodbc.Connection:
    """Conexão por Managed Identity — sem usuário e senha em lugar nenhum.

    A connection string é montada a partir de SQL_SERVER_FQDN e
    SQL_DATABASE — os dois nomes que o Bicep deste projeto provisiona (ver
    infra/main.bicep), mesmo padrão do coletor e do sync-expectations.
    """
    token = credential.get_token(c.SQL_SCOPE).token
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    SQL_COPT_SS_ACCESS_TOKEN = 1256  # constante do driver ODBC

    connection_string = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{os.environ['SQL_SERVER_FQDN']},1433;"
        f"Database={os.environ['SQL_DATABASE']};Encrypt=yes;"
    )
    return pyodbc.connect(
        connection_string,
        attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct},
    )


def read_watermark(conn: pyodbc.Connection) -> datetime:
    """Watermark atual, ou a época semeada se a linha nunca avançou."""
    row = conn.cursor().execute(
        "SELECT replicated_watermark FROM tbl_ctl_replication WHERE target = ?",
        c.REPLICATION_TARGET,
    ).fetchone()
    if row is None:
        logger.warning("tbl_ctl_replication sem linha para '%s'; usando época",
                       c.REPLICATION_TARGET)
        return datetime.fromisoformat(c.EPOCH_WATERMARK)
    return row[0]


def write_watermark(conn: pyodbc.Connection, watermark: datetime) -> None:
    conn.cursor().execute(
        "UPDATE tbl_ctl_replication SET replicated_watermark = ? WHERE target = ?",
        watermark, c.REPLICATION_TARGET,
    )
    conn.commit()


def read_incremental(
    conn: pyodbc.Connection, table: c.SourceTable, watermark: datetime,
) -> list[tuple]:
    """SELECT parametrizado — o filtro nunca entra por interpolação."""
    columns = ", ".join(table.columns)
    sql = f"SELECT {columns} FROM {table.hub_table} WHERE {table.timestamp_column} > ?"
    if table.extra_filter:
        sql += f" AND {table.extra_filter}"
    return conn.cursor().execute(sql, watermark).fetchall()


# ---------------------------------------------------------------------------
# Staging em CSV
# ---------------------------------------------------------------------------

def rows_to_csv(rows: list[tuple], columns: tuple[str, ...]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(
            value.isoformat() if isinstance(value, datetime) else value
            for value in row
        )
    return buffer.getvalue().encode("utf-8")


_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


def staging_filename(table: c.SourceTable, previous_watermark: datetime) -> str:
    """Determinístico por tabela e por watermark anterior — ver a nota no
    topo do arquivo sobre por que um nome aleatório quebraria o retry."""
    safe = _UNSAFE_FILENAME_CHARS.sub("_", previous_watermark.isoformat())
    return f"{table.hub_table}_{safe}.csv"


# ---------------------------------------------------------------------------
# Databricks — Files API e Statement Execution API, as duas HTTPS públicas.
# ---------------------------------------------------------------------------

def upload_to_volume(
    client: httpx.Client, token: str, host: str, filename: str, content: bytes,
) -> None:
    path = f"{c.STAGING_VOLUME}/{filename}"
    resp = client.put(
        f"{host}{c.FILES_API_PATH}{path}",
        headers={"Authorization": f"Bearer {token}"},
        content=content,
        params={"overwrite": "true"},
    )
    resp.raise_for_status()


def run_statement(
    client: httpx.Client, token: str, host: str, warehouse_id: str, statement: str,
) -> dict:
    """Executa uma instrução via Statement Execution API, com polling se
    ela não terminar dentro do wait síncrono máximo aceito pela API (50s).
    """
    resp = client.post(
        f"{host}{c.STATEMENT_API_PATH}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "warehouse_id": warehouse_id,
            "statement": statement,
            "wait_timeout": c.STATEMENT_WAIT_TIMEOUT,
        },
    )
    resp.raise_for_status()
    result = resp.json()

    elapsed = 0
    while result.get("status", {}).get("state") in ("PENDING", "RUNNING"):
        if elapsed >= c.STATEMENT_MAX_POLL_SECONDS:
            raise TimeoutError(
                f"statement {result.get('statement_id')} não terminou em "
                f"{c.STATEMENT_MAX_POLL_SECONDS}s"
            )
        time.sleep(c.STATEMENT_POLL_INTERVAL_SECONDS)
        elapsed += c.STATEMENT_POLL_INTERVAL_SECONDS
        resp = client.get(
            f"{host}{c.STATEMENT_API_PATH}/{result['statement_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        result = resp.json()

    state = result.get("status", {}).get("state")
    if state != "SUCCEEDED":
        raise RuntimeError(f"statement falhou: {result.get('status')}")
    return result


def build_copy_into(schema: str, table: c.SourceTable, filename: str) -> str:
    """COPY INTO por nome de arquivo explícito — nunca varre o volume
    inteiro, o que poderia recarregar algo que uma limpeza externa ainda
    não removeu. `header='true'` casa colunas por nome, não por posição:
    protege contra o CSV e `constants.SOURCE_TABLES` divergirem em ordem.
    """
    return (
        f"COPY INTO {schema}.{table.delta_table} "
        f"FROM '{c.STAGING_VOLUME}' "
        f"FILEFORMAT = CSV "
        f"FILES = ('{filename}') "
        f"FORMAT_OPTIONS ('header' = 'true', 'inferSchema' = 'true')"
    )


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableResult:
    table: c.SourceTable
    rows_read: int
    max_timestamp: datetime | None  # None se a tabela não trouxe linha nesta execução


def replicate_table(
    sql_conn: pyodbc.Connection, http_client: httpx.Client, token: str,
    host: str, warehouse_id: str, table: c.SourceTable, watermark: datetime,
) -> TableResult:
    rows = read_incremental(sql_conn, table, watermark)
    if not rows:
        logger.info("%s: nada novo desde %s", table.hub_table, watermark)
        return TableResult(table, 0, None)

    filename = staging_filename(table, watermark)
    upload_to_volume(http_client, token, host, filename, rows_to_csv(rows, table.columns))

    for schema in (c.RAW_SCHEMA, c.CLEANSED_SCHEMA):
        run_statement(
            http_client, token, host, warehouse_id,
            build_copy_into(schema, table, filename),
        )

    ts_index = table.columns.index(table.timestamp_column)
    max_timestamp = max(row[ts_index] for row in rows)

    logger.info("%s: %d linhas replicadas, novo máximo %s",
                table.hub_table, len(rows), max_timestamp)
    return TableResult(table, len(rows), max_timestamp)


def compute_new_watermark(
    results: list[TableResult], previous: datetime
) -> datetime:
    """O mínimo dos máximos — nunca o máximo, e nunca regride.

    Uma tabela sem linha nova nesta execução não trava o avanço: ela
    participa do mínimo pelo seu próprio `previous`, não fica ausente do
    cálculo — senão, uma tabela parada indefinidamente deixaria de segurar
    o avanço das demais, que é exatamente o que este mínimo existe para
    evitar. O que ele impede é o caso em que uma tabela avançou muito e
    outra falhou no meio da leitura: sem o mínimo, o watermark avançaria
    pela mais rápida, e a purga apagaria da mais lenta um período nunca
    confirmado replicado.
    """
    effective = [
        r.max_timestamp if r.max_timestamp is not None else previous
        for r in results
    ]
    return max(previous, min(effective))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    databricks_host = os.environ["DATABRICKS_HOST"].rstrip("/")
    warehouse_id = os.environ["DATABRICKS_WAREHOUSE_ID"]
    credential = DefaultAzureCredential(
        managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID")
    )

    with connect_sql(credential) as sql_conn, \
         httpx.Client(timeout=60) as http_client:
        watermark = read_watermark(sql_conn)
        token = credential.get_token(c.DATABRICKS_SCOPE).token

        results = [
            replicate_table(
                sql_conn, http_client, token, databricks_host,
                warehouse_id, table, watermark,
            )
            for table in c.SOURCE_TABLES
        ]

        new_watermark = compute_new_watermark(results, watermark)
        if new_watermark > watermark:
            write_watermark(sql_conn, new_watermark)
            logger.info("watermark avançado de %s para %s", watermark, new_watermark)
        else:
            logger.info("nenhuma tabela avançou; watermark mantido em %s", watermark)


if __name__ == "__main__":
    main()
