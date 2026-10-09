"""Constantes da API."""

# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

#: Nomes das variáveis que carregam os tokens. Os valores vêm do Key Vault
#: por referência, nunca ficam no código nem no App Settings em texto claro.
VAR_TOKEN_READ = "TOKEN_READ"
VAR_TOKEN_WRITE = "TOKEN_WRITE"

SCOPE_READ = "read"
SCOPE_WRITE = "write"

#: Prefixo do texto de uma Key Vault reference. Se a referência não resolve,
#: o App Setting fica com o texto literal em vez do segredo — e aceitá-lo
#: como token significaria aceitar um valor previsível, pior do que não ter
#: token nenhum. Por isso o token que começa com isto é recusado.
KEY_VAULT_REFERENCE_PREFIX = "@Microsoft.KeyVault"

# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------

VAR_SQL_CONNECTION = "SQL_CONNECTION_STRING"
SQL_SCOPE = "https://database.windows.net/.default"

#: Constante do driver ODBC para injetar o token de acesso.
SQL_COPT_SS_ACCESS_TOKEN = 1256

# ---------------------------------------------------------------------------
# Paginação
# ---------------------------------------------------------------------------

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000

# ---------------------------------------------------------------------------
# Regras das tabelas de configuração
# ---------------------------------------------------------------------------

#: `config_source` das expectativas criadas por esta API. O sincronizador de
#: expectativas só atualiza e desativa as linhas que ele mesmo criou (`AUTO`
#: e `YAML`), então este é o único valor em que o cadastro feito aqui
#: sobrevive à próxima sincronização.
EXPECTATION_CONFIG_SOURCE = "MANUAL"

#: Mínimo de nós esperados de um Self-Hosted Integration Runtime. Um IR
#: monitorado tem pelo menos um nó; abaixo disso o cadastro não descreve
#: nada que o coletor possa comparar. Mesmo limite da restrição da tabela
#: no hub.
MIN_EXPECTED_NODES = 1
