"""Constantes do sincronizador de expectativas de execução."""

# ---------------------------------------------------------------------------
# Escopos de token
# ---------------------------------------------------------------------------

#: Resource ID fixo do Azure Databricks no Microsoft Entra. Não é segredo e
#: não varia por tenant: identifica o serviço para o qual o token é emitido.
DATABRICKS_RESOURCE_ID = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"
DATABRICKS_SCOPE = f"{DATABRICKS_RESOURCE_ID}/.default"

AZURE_SCOPE = "https://management.azure.com/.default"
SQL_SCOPE = "https://database.windows.net/.default"


# ---------------------------------------------------------------------------
# Política operacional
# ---------------------------------------------------------------------------

#: Margem entre o horário esperado e o momento de considerar a execução
#: ausente. Não vem da origem: é decisão de operação.
DEFAULT_TOLERANCE_MIN = 15

#: Fuso usado quando a origem não declara o seu.
DEFAULT_TIMEZONE = "America/Sao_Paulo"

# ---------------------------------------------------------------------------
# Fonte da configuração (tbl_cfg_execution_expectations.config_source)
# ---------------------------------------------------------------------------

CONFIG_SOURCE_AUTO = "AUTO"

# ---------------------------------------------------------------------------
# tbl_cfg_execution_expectations.written_by — granularidade maior que
# config_source: distingue, dentro do mesmo config_source='AUTO', qual dos
# dois ramos (Databricks ou ADF) gravou a linha.
# ---------------------------------------------------------------------------

WRITTEN_BY_DATABRICKS = "sync_execution_expectations:databricks"
WRITTEN_BY_ADF = "sync_execution_expectations:adf"
