"""Constantes do publicador de eventos."""

# ---------------------------------------------------------------------------
# Origem — Databricks, só por HTTPS público (Statement Execution API).
# Mesma assimetria que já sustenta hub-replication e sync-expectations: quem
# inicia a conexão é sempre o lado de fora, nunca o compute do Databricks —
# por isso não importa se o workspace tem VNet injection. Ver a nota no topo
# de hub_publisher.py.
# ---------------------------------------------------------------------------

#: Resource ID fixo do Azure Databricks no Microsoft Entra — o mesmo usado
#: por sync_execution_expectations.py e hub_replication.py, para o mesmo
#: tipo de chamada (REST do plano de controle/SQL, nunca JDBC).
DATABRICKS_RESOURCE_ID = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"
DATABRICKS_SCOPE = f"{DATABRICKS_RESOURCE_ID}/.default"

STATEMENT_API_PATH = "/api/2.0/sql/statements"

#: Fila lida por este job. Precisa ser a mesma que os jobs de avaliação
#: escrevem — se um nome mudar, muda nos dois lugares.
STAGING_EVENTS_TABLE = "prd_governance.corp_cleansed.tbl_dbr_it_observability_events"

#: Colunas lidas de STAGING_EVENTS_TABLE, nesta ordem — o SELECT em
#: read_pending_events() e o parsing da resposta da Statement Execution API
#: em _rows_from_result() dependem desta ordem exata.
#:
#: `domain` é lido mas nunca repassado a `resolve_event()`: quem decide o
#: domínio do incidente é a própria função, a partir de `tbl_cfg_domain_scope`
#: (§4.3) — o corpo que o antigo hub_publisher enviava para a API também
#: carregava `domain` sem a API jamais usá-lo (repository.resolve_event não
#: o mapeava). Mantido aqui só por fidelidade ao comportamento anterior;
#: nunca foi corrigido de propósito, é uma lacuna preexistente.
EVENT_COLUMNS = (
    "source", "result", "natural_key", "domain", "severity",
    "error_code", "description", "source_event_id", "observed_at",
)

#: Statement Execution API é assíncrona; o wait síncrono máximo aceito pela
#: própria API é 50s. Acima disso, o job faz polling por conta própria —
#: mesmo padrão de hub_replication.py.
STATEMENT_WAIT_TIMEOUT = "50s"
STATEMENT_POLL_INTERVAL_SECONDS = 3
STATEMENT_MAX_POLL_SECONDS = 300

#: Máximo de eventos por execução, para o ciclo ter duração previsível. O
#: que sobrar vai no ciclo seguinte — mesmo limite de quando este job ainda
#: rodava dentro do Databricks.
BATCH_LIMIT = 500

# ---------------------------------------------------------------------------
# Destino — Azure SQL, por pyodbc (mesmo padrão do coletor, do
# sync-expectations e do hub-replication).
# ---------------------------------------------------------------------------

SQL_SCOPE = "https://database.windows.net/.default"
