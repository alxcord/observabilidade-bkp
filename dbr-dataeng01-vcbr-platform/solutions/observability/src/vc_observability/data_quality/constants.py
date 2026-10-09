"""Constantes do motor de Data Quality.

Nomes de tabelas e valores de dominio ficam aqui. Se um nome mudar, este e
o unico arquivo a editar.
"""

# ---------------------------------------------------------------------------
# Tabelas de destino
# ---------------------------------------------------------------------------

#: Historico completo dos resultados. Uma linha por regra por execucao.
RESULTS_TABLE = "prd_governance.corp_cleansed.tbl_dbr_it_data_quality_results"

#: Fila de eventos a enviar ao hub. O motor so escreve aqui.
STAGING_EVENTS_TABLE = "prd_governance.corp_cleansed.tbl_dbr_it_observability_events"


# ---------------------------------------------------------------------------
# Estados do resultado
#
# Duas dimensoes distintas, que nao podem ser colapsadas em um campo so:
# se a validacao conseguiu rodar, e se o dado esta bom. Um erro de
# infraestrutura contado como problema de qualidade contamina todo
# indicador construido em cima.
# ---------------------------------------------------------------------------

#: A validacao executou?
STATUS_SUCCESS = "SUCCESS"
STATUS_ERROR = "ERROR"
STATUS_NOT_EXECUTED = "NOT_EXECUTED"

#: O dado esta bom?
OUTCOME_PASS = "PASS"
OUTCOME_WARN = "WARN"
OUTCOME_FAIL = "FAIL"
OUTCOME_UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Tipos de regra suportados
# ---------------------------------------------------------------------------

TYPE_NOT_NULL = "not_null"
TYPE_UNIQUE = "unique"
TYPE_DOMAIN = "domain"
TYPE_RANGE = "range"
TYPE_REFERENCE = "reference"
TYPE_EXPRESSION = "expression"

SUPPORTED_TYPES = frozenset(
    {
        TYPE_NOT_NULL,
        TYPE_UNIQUE,
        TYPE_DOMAIN,
        TYPE_RANGE,
        TYPE_REFERENCE,
        TYPE_EXPRESSION,
    }
)


# ---------------------------------------------------------------------------
# Publicacao no hub
# ---------------------------------------------------------------------------

SOURCE_DATA_QUALITY = "DATA_QUALITY"
ERROR_CODE_RULE_FAILED = "RULE_FAILED"
ERROR_CODE_RULE_NOT_EVALUATED = "RULE_NOT_EVALUATED"

DEFAULT_SEVERITY = "MEDIUM"
DEFAULT_DOMAIN = "DEFAULT"

#: Percentual de linhas invalidas tolerado quando a regra nao declara limite.
DEFAULT_THRESHOLD_PCT = 0.0
