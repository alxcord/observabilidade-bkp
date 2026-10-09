"""Envia ao hub os eventos que os jobs de avaliação deixaram em staging.

Este job existe para separar duas responsabilidades que não podem se
misturar: quem avalia o dado escreve em Delta e pronto; quem fala com o
hub é outro processo. Assim uma indisponibilidade do hub nunca derruba um
job de negócio nem impede a avaliação de acontecer (mesmo princípio do
antigo job Databricks que este substitui — ver "Histórico" abaixo).

Fluxo:

    tbl_dbr_it_observability_events (Databricks)  -->  resolve_event() (Azure SQL)  -->  marca sent_at

  IMPORTANTE — por que isto roda em Container Apps, não em Databricks:
  a primeira versão deste job era um job Databricks lendo a staging por
  Spark e fazendo `POST /events` numa API (Azure Functions) que then
  chamava `resolve_event()` — porque um workspace **sem VNet injection**
  roda o compute numa VNet gerenciada pela Microsoft, sem rota possível até
  o Private Endpoint do Azure SQL: não é configuração faltando, é
  impossibilidade estrutural.

  A saída usa a mesma assimetria que já sustenta `hub_replication.py` e
  `sync_execution_expectations.py` — só que na direção contrária. As APIs
  REST do Databricks (aqui, a Statement Execution API) são HTTPS públicas,
  alcançáveis de qualquer lugar, **independente** de VNet injection no
  workspace — porque quem inicia a conexão é sempre o lado de fora, nunca
  o compute do Databricks. Este job lê a staging pela Statement Execution
  API, chama `event_resolution.resolve_event()` direto contra o Azure SQL
  por pyodbc (mesmo padrão do coletor, do sync-expectations e do
  hub-replication), e não precisa mais de uma API HTTP no meio para
  atravessar a rede.

O envio é at-least-once: se o processo cair depois de `resolve_event()`
confirmar mas antes de marcar `sent_at`, o evento é reenviado no próximo
ciclo. Por isso cada evento carrega um `source_event_id` determinístico, e
`resolve_event()` descarta repetição (§1 do seu docstring).

Histórico: até 2026-09, este job rodava dentro do Databricks
(`src/databricks/hub_publisher/`, removido) e enviava por HTTPS para
`POST /api/events` na Azure Function (repositório próprio), que por sua vez chamava
`resolve_event()`. A rota foi removida da API: nada mais precisa dela para
escrever no hub, e a decisão de estado nunca morou ali (ver docstring de
`event_resolution.resolve_event`).

Uso:

    python hub_publisher.py --limit 500

Configuração por variável de ambiente (mesmo padrão de
`hub_replication.py`: quem provisiona os valores é o Bicep, não uma
chamada manual com flags):

    SQL_SERVER_FQDN          servidor do hub, ex.: sqlsrv-observ-vcbr.database.windows.net
    SQL_DATABASE             nome do banco
    DATABRICKS_HOST          URL do workspace, ex.: https://<workspace>.azuredatabricks.net
    DATABRICKS_WAREHOUSE_ID  SQL Warehouse que atende a Statement Execution API
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import struct
import time
from datetime import datetime, timezone

import httpx
import pyodbc
from azure.identity import DefaultAzureCredential

import constants as c

# event_resolution.py vive em src/shared/, fora deste projeto — o Dockerfile
# copia o arquivo para dentro desta pasta no build (ver comentário no topo
# do Dockerfile), o mesmo mecanismo que observability-collector já usa.
# Rodando fora do container (testes, dev local), o import cai no `except` e
# aponta para src/shared/ diretamente, já que o repositório inteiro está
# disponível nesses dois casos.
try:
    from event_resolution import resolve_event
except ImportError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
    from event_resolution import resolve_event

logger = logging.getLogger("hub-publisher")


# ---------------------------------------------------------------------------
# Azure SQL — pyodbc, Managed Identity. Mesmo padrão do coletor, do
# sync-expectations e do hub-replication: sem usuário e senha em lugar
# nenhum, o token entra pelo atributo do driver ODBC.
# ---------------------------------------------------------------------------

def connect_sql(credential: DefaultAzureCredential) -> pyodbc.Connection:
    """Conexão por Managed Identity — sem usuário e senha em lugar nenhum."""
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


# ---------------------------------------------------------------------------
# Databricks — só Statement Execution API, HTTPS público. Mesmo helper de
# polling que hub_replication.py usa para o COPY INTO — duplicado aqui de
# propósito: os dois projetos são independentes (src/README.md), sem
# biblioteca compartilhada além de event_resolution.py.
# ---------------------------------------------------------------------------

def run_statement(
    client: httpx.Client, token: str, host: str, warehouse_id: str, statement: str,
) -> dict:
    """Executa uma instrução via Statement Execution API, com polling se ela
    não terminar dentro do wait síncrono máximo aceito pela API (50s)."""
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


def _parse_observed_at(value: str) -> datetime:
    """Converte o TIMESTAMP devolvido pela Statement Execution API.

    A API devolve TIMESTAMP em JSON como texto — aqui assumido
    "YYYY-MM-DD HH:MM:SS[.ffffff]" (formato usual do Databricks, sem fuso
    explícito porque a coluna é gravada a partir de `current_timestamp()`
    num warehouse configurado em UTC). **Não validado contra uma resposta
    real** — mesma lacuna que o README da Observabilidade já registra para outras
    premissas de schema deste projeto; testar contra o warehouse real antes
    de operar em produção.
    """
    text = value.replace(" ", "T", 1) if " " in value and "T" not in value else value
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _rows_from_result(result: dict) -> list[dict]:
    """Converte `data_array` da Statement Execution API em dicts, na ordem
    de `EVENT_COLUMNS` — a mesma ordem do SELECT em `read_pending_events`."""
    data = result.get("result", {}).get("data_array", []) or []
    rows = []
    for values in data:
        row = dict(zip(c.EVENT_COLUMNS, values))
        row["observed_at"] = _parse_observed_at(row["observed_at"])
        rows.append(row)
    return rows


def read_pending_events(
    client: httpx.Client, token: str, host: str, warehouse_id: str, limit: int,
) -> list[dict]:
    """Eventos ainda não enviados, dos mais antigos para os mais novos."""
    columns = ", ".join(c.EVENT_COLUMNS)
    statement = (
        f"SELECT {columns} FROM {c.STAGING_EVENTS_TABLE}"
        " WHERE sent_at IS NULL"
        " ORDER BY observed_at"
        f" LIMIT {int(limit)}"
    )
    result = run_statement(client, token, host, warehouse_id, statement)
    rows = _rows_from_result(result)
    logger.info("%d eventos pendentes", len(rows))
    return rows


#: Alfabeto seguro para um `source_event_id` dentro de um IN (...) montado
#: por concatenação — mesmo cuidado que `staging_filename()` já aplica em
#: hub_replication.py. `source_event_id` é gerado pelos próprios jobs de
#: avaliação (nunca texto livre externo), mas a checagem nunca deve
#: depender só disso.
_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def mark_sent(
    client: httpx.Client, token: str, host: str, warehouse_id: str, ids: list[str],
) -> None:
    """Marca como enviados só os eventos que `resolve_event()` confirmou.

    O que não foi confirmado continua pendente e é reenviado no próximo
    ciclo — é o que torna uma falha no meio do lote inofensiva.
    """
    if not ids:
        return

    unsafe = [i for i in ids if _UNSAFE_ID_CHARS.search(i)]
    if unsafe:
        raise ValueError(f"source_event_id fora do alfabeto esperado: {unsafe}")

    id_list = ", ".join(f"'{i}'" for i in ids)
    statement = (
        f"UPDATE {c.STAGING_EVENTS_TABLE} SET sent_at = current_timestamp()"
        f" WHERE source_event_id IN ({id_list})"
    )
    run_statement(client, token, host, warehouse_id, statement)
    logger.info("%d eventos marcados como enviados", len(ids))


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------

def publish(
    sql_conn: pyodbc.Connection,
    http_client: httpx.Client,
    token: str,
    host: str,
    warehouse_id: str,
    limit: int,
) -> int:
    """Lê os pendentes no Databricks, resolve cada um direto no hub, e
    devolve quantos foram confirmados."""
    events = read_pending_events(http_client, token, host, warehouse_id, limit)
    sent: list[str] = []

    for event in events:
        try:
            resolve_event(
                sql_conn,
                source=event["source"],
                result=event["result"],
                natural_key=event["natural_key"],
                severity=event["severity"],
                error_code=event["error_code"],
                error_description=event["description"],
                source_event_id=event["source_event_id"],
                observed_at=event["observed_at"],
            )
        except pyodbc.OperationalError:
            # Hub fora do ar ou conexão perdida: para o ciclo aqui. Insistir
            # nos demais só acumularia o mesmo erro, e o que ficou pendente
            # será reenviado na próxima execução — mesmo raciocínio que a
            # versão anterior aplicava a timeout de rede.
            logger.exception(
                "falha de conexão ao resolver evento; interrompendo o ciclo"
            )
            break
        except Exception:
            # Falha de dado (ex.: origem desconhecida em tbl_ref_source) não
            # é falha de conectividade — não trava os demais eventos do
            # lote. Este evento continua pendente e é tentado de novo no
            # próximo ciclo.
            logger.exception(
                "falha ao resolver evento %s; será reenviado",
                event["source_event_id"],
            )
        else:
            sent.append(event["source_event_id"])

    mark_sent(http_client, token, host, warehouse_id, sent)
    return len(sent)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=c.BATCH_LIMIT)
    args = parser.parse_args()

    databricks_host = os.environ["DATABRICKS_HOST"].rstrip("/")
    warehouse_id = os.environ["DATABRICKS_WAREHOUSE_ID"]
    credential = DefaultAzureCredential(
        managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID")
    )

    with connect_sql(credential) as sql_conn, \
         httpx.Client(timeout=60) as http_client:
        token = credential.get_token(c.DATABRICKS_SCOPE).token
        sent = publish(
            sql_conn, http_client, token, databricks_host, warehouse_id, args.limit
        )
        logger.info("%d eventos publicados no hub", sent)


if __name__ == "__main__":
    main()
