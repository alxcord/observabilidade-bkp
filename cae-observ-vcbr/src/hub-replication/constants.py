"""Constantes da replicação do hub para Delta."""

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Origem — Azure SQL, por pyodbc (mesmo padrão do coletor e do sync-expectations)
# ---------------------------------------------------------------------------

SQL_SCOPE = "https://database.windows.net/.default"


@dataclass(frozen=True)
class SourceTable:
    """Uma tabela do hub replicada para Delta, incremental por timestamp."""

    #: Nome no Azure SQL.
    hub_table: str
    #: Coluna usada como watermark — sempre monotônica, nunca UPDATE depois
    #: de escrita (ver nota em `read_incremental`).
    timestamp_column: str
    #: Filtro adicional aplicado no SELECT de origem, se houver.
    extra_filter: str | None
    #: Nome da tabela Delta de destino (mesmo nome nas duas camadas).
    delta_table: str
    #: Ordem e nomes das colunas no CSV de staging — precisa bater com a
    #: ordem do SELECT e com a ordem esperada pelo COPY INTO.
    columns: tuple[str, ...]


# As tabelas que a purga do hub (documento §10) exige estarem replicadas
# antes de apagar. Nenhuma delas é alterada depois de escrita: `tbl_executions`
# é log append-only; `tbl_problems` só entra aqui filtrado por
# `incident_status = 'RESOLVED'`, e um problema resolvido não muda mais
# (não há reabertura no desenho — um novo FAIL abre um incidente novo, com
# novo `problem_id`).
#
# Uma tabela permanentemente vazia NUNCA pode entrar aqui. O watermark novo
# é o mínimo dos máximos, e uma tabela sem linha participa desse mínimo pelo
# watermark anterior — então ela o fixaria em `previous` para sempre, e a
# purga, que exige `< replicated_watermark`, jamais apagaria nada. Foi
# exatamente o que acontecia com `quality_results`, removida do hub.
SOURCE_TABLES = (
    SourceTable(
        hub_table="tbl_executions",
        timestamp_column="observed_at",
        extra_filter=None,
        delta_table="tbl_db_it_hub_executions",
        columns=("source", "scope", "natural_key", "run_id", "run_name",
                 "status", "result_state", "capture_source", "observed_at"),
    ),
    SourceTable(
        hub_table="tbl_problems",
        timestamp_column="closed_at",
        extra_filter="incident_status = 'RESOLVED'",
        delta_table="tbl_db_it_hub_problems",
        columns=("problem_id", "source", "correlation_key", "natural_key",
                 "key_rule", "workspace_id", "domain", "incident_status",
                 "severity", "error_code", "error_description",
                 "opening_run_id", "opened_at", "closing_run_id",
                 "closed_at", "occurrence_count"),
    ),
)

# ---------------------------------------------------------------------------
# Destino — Databricks, só por HTTPS público (Files API + Statement
# Execution API). Nunca JDBC: um workspace sem VNet injection não tem rota
# de rede até o Private Endpoint do Azure SQL, e é exatamente esse caminho
# que este job existe para não precisar. Ver a nota no topo de
# hub_replication.py.
# ---------------------------------------------------------------------------

#: Resource ID fixo do Azure Databricks no Microsoft Entra — o mesmo usado
#: por sync_execution_expectations.py, para o mesmo tipo de chamada
#: (REST do plano de controle, nunca o plano de dados/compute).
DATABRICKS_RESOURCE_ID = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"
DATABRICKS_SCOPE = f"{DATABRICKS_RESOURCE_ID}/.default"

FILES_API_PATH = "/api/2.0/fs/files"
STATEMENT_API_PATH = "/api/2.0/sql/statements"

#: Volume do Unity Catalog usado como pouso do CSV antes do COPY INTO.
#: Arquivo antigo pode ser apagado por rotina externa; o job não depende
#: de limpeza própria porque COPY INTO já não recarrega o que processou.
STAGING_VOLUME = "/Volumes/prd_governance/corp_raw/hub_replication_staging"

RAW_SCHEMA = "prd_governance.corp_raw"
CLEANSED_SCHEMA = "prd_governance.corp_cleansed"

#: Statement Execution API é assíncrona; synchronous wait máximo aceito
#: pela própria API é 50s. Acima disso, o job faz polling por conta própria.
STATEMENT_WAIT_TIMEOUT = "50s"
STATEMENT_POLL_INTERVAL_SECONDS = 3
STATEMENT_MAX_POLL_SECONDS = 300

# ---------------------------------------------------------------------------
# Controle de watermark (tbl_ctl_replication)
# ---------------------------------------------------------------------------

REPLICATION_TARGET = "delta"

#: Piso quando ainda não há watermark gravado — corresponde ao valor semeado
#: em schema_definition.py (hub-schema-init).
EPOCH_WATERMARK = "1900-01-01T00:00:00.000+00:00"
