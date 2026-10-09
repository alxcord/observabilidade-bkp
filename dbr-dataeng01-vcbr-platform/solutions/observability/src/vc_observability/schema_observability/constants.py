"""Constantes do coletor de mudanca de schema.

Mesma logica do `asset_observability/constants.py`: tudo que a governanca
pode renomear fica aqui, e nao em outro arquivo.
"""

# ---------------------------------------------------------------------------
# Tags de governanca lidas das tabelas monitoradas
#
# So as duas de identidade — dominio e criticidade. Ao contrario do
# freshness, a deteccao de schema NAO depende de `vc_refresh_frequency`: uma
# tabela sem promessa de atualizacao ainda pode ter a estrutura quebrada por
# baixo de quem consome. Restringir ao mesmo subconjunto do freshness
# deixaria de fora justamente as tabelas de referencia, atualizadas raro,
# que costumam ser as mais antigas e as mais dificeis de re-taggear depois
# que alguem descobre a lacuna.
# ---------------------------------------------------------------------------

TAG_DATA_DOMAIN = "vc_data_domain"
TAG_CRITICALITY = "vc_criticality"


# ---------------------------------------------------------------------------
# Sobrescrita e consumidores declarados em YAML
#
# Mesmo arquivo do asset_observability — `config/shared/assets/monitored-assets.yaml`
# — de proposito. Um ativo so tem UMA lista de consumidores conhecidos, e
# manter esse dado em dois arquivos e o tipo de duplicacao que diverge em
# silencio.
# ---------------------------------------------------------------------------

DEFAULT_MONITORED_ASSETS_PATH = "config/shared/assets/monitored-assets.yaml"


# ---------------------------------------------------------------------------
# Tabelas de destino
# ---------------------------------------------------------------------------

#: Um registro por coluna por execucao. E o snapshot bruto — a fonte da
#: comparacao entre versoes; NUNCA e apagado nem sobrescrito.
#:
#: SCD-lite, nao SCD Tipo 2 completo (ver Cenario 3, doc de referencia,
#: onde o desenho original previa `valid_from`/`valid_to`/`is_current`). O
#: MVP grava so o snapshot corrente por execucao — o "corrente" de cada
#: coluna e a linha de `observed_at` mais recente — e a comparacao usa
#: SEMPRE a execucao anterior, nunca uma janela mais larga. E a mesma
#: simplificacao que `OBSERVATIONS_TABLE` do freshness ja faz. Evoluir para
#: SCD Tipo 2 completo (consulta "qual era o schema em 15/03") fica para
#: quando houver um consumidor real dessa consulta — hoje ninguem pediu.
SNAPSHOT_TABLE = "prd_governance.corp_cleansed.tbl_dbr_it_schema_snapshot"

#: A tabela de log propriamente dita: uma linha por MUDANCA detectada,
#: nunca por coluna observada. E o equivalente, neste cenario, ao
#: `tbl_observability_it_mudanca_schema` desenhado no Cenario 3 — a "copia
#: das system tables para uma tabela de log" que o modelo de dados daquele
#: cenario ja previa e que faltava ligar aqui.
CHANGES_TABLE = "prd_governance.corp_cleansed.tbl_dbr_it_schema_changes"

#: Fila de eventos a enviar ao hub. Tabela fisica compartilhada com
#: asset_observability e data_quality — mesmo esquema de colunas, mesma
#: convencao (ver docs/Tabelas-Databricks.md, secao 3).
STAGING_EVENTS_TABLE = "prd_governance.corp_cleansed.tbl_dbr_it_observability_events"


# ---------------------------------------------------------------------------
# Classificacao da mudanca (Cenario 3, doc de referencia, secao 15.3)
# ---------------------------------------------------------------------------

CHANGE_COLUMN_ADDED = "COLUMN_ADDED"
CHANGE_COLUMN_DROPPED = "COLUMN_DROPPED"
CHANGE_TYPE_CHANGED = "TYPE_CHANGED"
CHANGE_NULLABILITY_TIGHTENED = "NULLABILITY_TIGHTENED"
CHANGE_NULLABILITY_RELAXED = "NULLABILITY_RELAXED"
CHANGE_TABLE_DROPPED = "TABLE_DROPPED"

CLASS_COMPATIVEL = "COMPATIVEL"
CLASS_CONTROLADA = "CONTROLADA"
CLASS_INCOMPATIVEL = "INCOMPATIVEL"
CLASS_CRITICA = "CRITICA"

#: Alerta so quando a classificacao e uma destas — e mesmo assim, so se o
#: ativo tiver consumidor conhecido (ver `has_known_consumers`). Compativel
#: e controlada vao so para o log; ninguem precisa ser acordado por uma
#: coluna nova.
ALERTABLE_CLASSIFICATIONS = frozenset({CLASS_INCOMPATIVEL, CLASS_CRITICA})

#: Pares (tipo_anterior, tipo_novo) tratados como alargamento seguro —
#: todo valor do tipo antigo continua representavel no novo. Comparacao
#: por prefixo antes do primeiro `(` — "decimal(10,2)" vira "decimal" — de
#: proposito: mudanca de precisao/escala dentro do mesmo tipo base fica
#: fora deste conjunto e cai em INCOMPATIVEL por padrao, porque nem toda
#: mudanca de precisao e alargamento (decimal(10,2) -> decimal(5,2) estreita).
SAFE_WIDENING = frozenset({
    ("tinyint", "smallint"),
    ("tinyint", "int"),
    ("tinyint", "bigint"),
    ("tinyint", "float"),
    ("tinyint", "double"),
    ("tinyint", "decimal"),
    ("smallint", "int"),
    ("smallint", "bigint"),
    ("smallint", "float"),
    ("smallint", "double"),
    ("smallint", "decimal"),
    ("int", "bigint"),
    ("int", "float"),
    ("int", "double"),
    ("int", "decimal"),
    ("bigint", "double"),
    ("bigint", "decimal"),
    ("float", "double"),
    ("date", "timestamp"),
})


# ---------------------------------------------------------------------------
# Severidade e dominio — mesma convencao do asset_observability
# ---------------------------------------------------------------------------

SEVERITY_BY_CRITICALITY = {
    "CRITICAL": "HIGH",
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "LOW": "LOW",
}
DEFAULT_SEVERITY = "MEDIUM"
DEFAULT_DOMAIN = "DEFAULT"


# ---------------------------------------------------------------------------
# Codigos de erro publicados no hub
# ---------------------------------------------------------------------------

SOURCE_SCHEMA = "SCHEMA"

ERROR_CODE_INCOMPATIBLE_CHANGE = "SCHEMA_INCOMPATIBLE_CHANGE"
ERROR_CODE_CRITICAL_CHANGE = "SCHEMA_CRITICAL_CHANGE"
