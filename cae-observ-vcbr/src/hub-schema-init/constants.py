"""Constantes do inicializador do schema do hub."""

SQL_SCOPE = "https://database.windows.net/.default"

#: Constante do driver ODBC para injetar o token de acesso.
SQL_COPT_SS_ACCESS_TOKEN = 1256

# ---------------------------------------------------------------------------
# Usuários de banco (passo --grant-users)
# ---------------------------------------------------------------------------

#: Identidades gerenciadas por ambiente. Em produção o nome não leva sufixo.
COLLECTOR_IDENTITY = "mi-observ-coletor{suffix}-vcbr"
API_IDENTITY = "mi-observ-api{suffix}-vcbr"

#: Grupos de rede com acesso administrativo (pedido do time de plataforma),
#: separados por vírgula em SQL_ADMIN_GROUPS; este é o padrão.
DEFAULT_ADMIN_GROUPS = (
    "Data Lake - Administrador de dados",
    "Data Lake - Sustentacao",
)

#: O que a API pode fazer. Ela lê incidentes só pelas views e faz CRUD só
#: nas tabelas de configuração; nunca grava incidente (especificação 01).
API_SELECT_ONLY = (
    "tbl_ref_source",
    "vw_open_problems",
    "vw_recently_closed_problems",
)
API_CRUD = (
    "tbl_cfg_workspaces",
    "tbl_cfg_alert_routing",
    "tbl_cfg_execution_expectations",
    "tbl_cfg_domain_scope",
    "tbl_cfg_integration_runtimes",
)
