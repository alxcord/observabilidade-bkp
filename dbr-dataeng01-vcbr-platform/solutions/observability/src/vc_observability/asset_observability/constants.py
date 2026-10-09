"""Constantes do coletor de freshness e volume.

Tudo que a governança pode renomear fica aqui: nomes de tags, nomes de
tabelas e catálogos. Se um nome mudar, este é o único arquivo a editar.
"""

import re

# ---------------------------------------------------------------------------
# Tags de governança lidas das tabelas monitoradas
# ---------------------------------------------------------------------------

#: Periodicidade esperada de atualização, no formato <número><unidade>:
#: "5min", "1h", "1d", "1w", "1m" (mês), "1y" (ano). Ver parse_frequency().
TAG_REFRESH_FREQUENCY = "vc_refresh_frequency"

#: Prazo máximo entre a atualização esperada e a real, no mesmo formato da
#: frequência. Quando ausente, vale o próprio período da frequência.
#:
#: Existe na taxonomia corporativa (Pilar 05, confirmado em 2026-10-06). A
#: distinção importa: a frequência é um fato descritivo do dado ("atualiza
#: de hora em hora"); o SLA é tolerância operacional ("atrasar até 2h é
#: aceitável"). Sem a segunda, alguém acaba distorcendo a primeira para
#: calar alerta, e a tag descritiva deixa de descrever a realidade.
TAG_FRESHNESS_SLA = "vc_freshness_sla"

#: Domínio de negócio, usado para rotear o alerta.
#: Aplicada no SCHEMA, não na tabela — ver read_tags_by_table().
TAG_DATA_DOMAIN = "vc_data_domain"

#: Criticidade do ativo. Define a severidade do incidente.
TAG_CRITICALITY = "vc_criticality"


# ---------------------------------------------------------------------------
# Ambientes e catálogos
# ---------------------------------------------------------------------------

ENV_DEV = "dev"
ENV_PRD = "prd"

#: Prefixo dos catálogos varridos em cada ambiente. Um job de dev nunca
#: observa catálogo de produção, e vice-versa.
CATALOG_PREFIX_BY_ENVIRONMENT = {
    ENV_DEV: "dev_",
    ENV_PRD: "prd_",
}

#: Nomes de target do bundle (databricks.yml) traduzidos para o ambiente.
ENVIRONMENT_BY_BUNDLE_TARGET = {
    "dev": ENV_DEV,
    "prod": ENV_PRD,
}

#: Org ID do workspace (spark.databricks.clusterUsageTags.clusterOwnerOrgId),
#: o mesmo número que aparece no host do databricks.yml. Usado só como
#: fallback quando o ambiente não é informado por --environment.
ENVIRONMENT_BY_WORKSPACE_ORG_ID = {
    "1886204700223023": ENV_DEV,
    "6114343631149730": ENV_PRD,
}

#: Schemas de sistema, nunca varridos na descoberta automática.
EXCLUDED_SCHEMAS = frozenset({"information_schema", "default"})

#: Prefixo de tabela interna de pipeline (materializações do Lakeflow/DLT),
#: nunca um ativo monitorável de verdade.
#:
#: Em cluster de acesso Assigned, `DESCRIBE HISTORY` nessas tabelas sempre
#: falha com `PERMISSION_DENIED` — a credencial do Unity Catalog para esse
#: tipo de tabela interna só é concedida em cluster Shared. Excluir aqui
#: evita o erro E a chamada desperdiçada, em vez de só tratar a falha.
INTERNAL_TABLE_PREFIX = "__"

#: Tabela de log de eventos de pipeline Lakeflow/DLT: `event_log_<uuid>`, com
#: o UUID do pipeline. Mesma categoria de tabela interna do prefixo `__`,
#: mas nomeada de outro jeito. O UUID no fim torna a regra precisa — um
#: `event_log_` qualquer, sem UUID, nao e excluido.
PIPELINE_EVENT_LOG_PATTERN = re.compile(
    r"^event_log_[0-9a-f]{8}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# Sobrescrita declarada em YAML
# ---------------------------------------------------------------------------

#: Caminho padrão do YAML de ativos, relativo à pasta da solução
#: (solutions/observability). O job do bundle passa o caminho absoluto em
#: --monitored-assets-path; este padrão serve à execução local.
DEFAULT_MONITORED_ASSETS_PATH = "config/shared/assets/monitored-assets.yaml"


# ---------------------------------------------------------------------------
# Tabelas de destino
#
# Os nomes abaixo sao so o nome da tabela. O catalogo vem do ambiente
# (GOVERNANCE_CATALOG_BY_ENVIRONMENT): dev grava em dev_governance e prd em
# prd_governance, nunca um no outro.
# ---------------------------------------------------------------------------

GOVERNANCE_CATALOG_BY_ENVIRONMENT = {
    ENV_DEV: "dev_governance",
    ENV_PRD: "prd_governance",
}

#: Schema das tabelas de destino, igual nos dois ambientes.
GOVERNANCE_SCHEMA = "corp_cleansed"

#: Histórico de observações. Uma linha por ativo por execução.
OBSERVATIONS_TABLE = "tbl_dbr_it_asset_observability"

#: Um registro por commit Delta de cada ativo monitorado.
#:
#: Grão diferente da tabela de observações: aquela tem um registro por
#: ativo por execução (snapshot), esta tem um por commit. Misturar os dois
#: estragaria as duas análises.
#:
#: FASE 1: este log só é escrito. Nenhuma regra de alerta consome ele
#: ainda — a ideia é medir as distribuições reais por algumas semanas e só
#: então escrever regra em cima de dado observado, em vez de chutar
#: limites e calibrar na base do falso positivo.
COMMIT_METRICS_TABLE = "tbl_dbr_it_asset_commit_metrics"

#: Retenção do log de commits. 13 meses para caber sazonalidade de
#: fechamento mensal e de virada de ano, que é onde moram as variações
#: difíceis de explicar. Aplicada no fim de cada execução por
#: purge_commit_metrics().
COMMIT_METRICS_RETENTION_MONTHS = 13

#: Quantos ativos sao coletados em paralelo (DESCRIBE DETAIL/HISTORY,
#: COUNT(*)) dentro de um mesmo schema.
#:
#: Cada chamada e um round-trip ao Unity Catalog, nao trabalho pesado de
#: cluster — por isso threads ajudam (o Python libera o GIL esperando a
#: resposta). O teto existe pelo lado oposto: disparar paralelismo alto
#: demais arrisca throttling do Unity Catalog (erros 429), que apareceriam
#: como falha por ativo, nao como aviso de limite. Comece baixo e meça
#: antes de subir.
ASSET_COLLECTION_WORKERS = 8

#: Teto de commits registrados por ativo por execução.
#:
#: Uma tabela alimentada por Structured Streaming gera um commit por
#: micro-batch — milhares por dia. Pela convenção, essas tabelas de landing
#: não são monitoradas (o monitoramento fica na tabela deduplicada a
#: jusante), mas convenção não é garantia: basta alguém taggear uma landing
#: por engano — ou por obediência ao padrão, que diz que toda tag é
#: obrigatória. Acima do teto, o job grava UMA linha agregada da janela e
#: registra aviso, em vez de milhares de linhas.
COMMIT_BATCH_CAP = 500

#: Operação sintética da linha agregada gravada quando o teto estoura.
OPERATION_AGGREGATED = "AGGREGATED"

#: Métricas de linha do `operationMetrics`, por operação Delta. Chave
#: ausente vira NULL, nunca zero: "não sei" e "foram zero linhas" são
#: respostas diferentes, e confundir as duas estraga a análise.
#:
#: `numTargetRowsCopied` são as linhas inalteradas reescritas junto num
#: MERGE. Sem ela, `numOutputRows` de um MERGE é enganoso: um MERGE que
#: insere 100 linhas pode reportar 1.200.000 de saída, porque reescreveu
#: arquivos inteiros.
COMMIT_METRIC_FIELDS = {
    "num_output_rows": "numOutputRows",
    "num_target_rows_inserted": "numTargetRowsInserted",
    "num_target_rows_updated": "numTargetRowsUpdated",
    "num_target_rows_deleted": "numTargetRowsDeleted",
    "num_target_rows_copied": "numTargetRowsCopied",
    "num_source_rows": "numSourceRows",
    "num_deleted_rows": "numDeletedRows",
    "num_updated_rows": "numUpdatedRows",
    "num_added_files": "numAddedFiles",
    "num_removed_files": "numRemovedFiles",
    "num_added_bytes": "numAddedBytes",
    "num_removed_bytes": "numRemovedBytes",
}


#: Fila de eventos a enviar ao hub. O job só escreve aqui; quem envia é
#: outro job (ver hub_publisher.py).
STAGING_EVENTS_TABLE = "tbl_dbr_it_observability_events"


#: Tags de governança aplicadas nas tabelas de destino deste job, via
#: ALTER TABLE ... SET TAGS, no bootstrap (ver ensure_destination_tables()
#: em asset_observability.py).
#:
#: `vc_business_unit` fica de fora de propósito: a governança ainda não
#: definiu o valor para este domínio, e não aplicar a tag é melhor do que
#: aplicar um chute que ninguém vai lembrar de corrigir depois.
#:
#: `vc_medallion_layer` também fica de fora daqui — não é um valor fixo,
#: e sim o próprio GOVERNANCE_SCHEMA: a tabela mora fisicamente em
#: corp_cleansed, e a tag não pode divergir do schema onde ela realmente
#: esta.
GOVERNANCE_TABLE_TAGS = {
    "vc_source_system": "databricks",
    "vc_source_object": "several",
    "vc_company": "vcbr",
    "vc_contains_pii": "false",
    "vc_criticality": "low",
    "vc_data_classification": "internal",
    "vc_data_domain": "it",
    "vc_data_quality_tier": "experimental",
    "vc_data_retention": "2y",
    "vc_lifecycle_status": "active",
    "vc_refresh_frequency": "1d",
}


# ---------------------------------------------------------------------------
# Regras de avaliação
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Regras de volume
#
# Não são tag: moram no YAML de ativos. A taxonomia corporativa não tem
# equivalente para a antiga `volume_tolerance_pct`, e um percentual único em
# tag nunca expressou o que o volume precisa — mínimo E máximo, percentual E
# absoluto, e exceção para expurgo mensal.
# ---------------------------------------------------------------------------

#: Métrica comparada entre observações.
#:
#: Registros, não bytes. `OPTIMIZE` compacta arquivos pequenos em grandes e
#: muda `sizeInBytes` em 20 a 40% sem uma linha de dado mudar. O problema não
#: é só o falso positivo: se o OPTIMIZE encolhe 25% no mesmo dia em que uma
#: carga falha e traz 20% menos linhas, as duas variações se cancelam, o
#: volume parece estável, e o incidente real nunca abre. Bytes continuam
#: gravados na observação — servem para diagnóstico e FinOps —, fora da regra.
METRIC_ROW_COUNT = "row_count"
METRIC_BYTES = "bytes"
VALID_METRICS = frozenset({METRIC_ROW_COUNT, METRIC_BYTES})

#: Faixa de variação usada quando o ativo não declara bloco `volume`.
#: Simétrica por não haver o que assumir sobre o ativo; declarar limites
#: assimétricos é justamente o ganho de sair da tag.
DEFAULT_VARIATION_MIN_PCT = -40.0
DEFAULT_VARIATION_MAX_PCT = 40.0

#: Mínimo absoluto padrão. Tabela monitorada vazia é sempre falha, e este é
#: o caso particular que expressa isso.
DEFAULT_ROW_COUNT_MIN = 1

#: Usado quando o ativo não declara nem SLA nem frequência reconhecível.
DEFAULT_FRESHNESS_SLA_MINUTES = 24 * 60

#: Minutos por unidade do formato de frequência. `min` precisa ser testada
#: antes de `m` na alternância do regex, senão "5min" casa como 5 meses.
#: Mês é 30 dias e ano é 365 — convenção, porque nenhum dos dois tem
#: duração fixa e o alerta não precisa de precisão de calendário.
MINUTES_PER_UNIT = {
    "min": 1,
    "h": 60,
    "d": 60 * 24,
    "w": 60 * 24 * 7,
    "m": 60 * 24 * 30,
    "y": 60 * 24 * 365,
}

#: Severidade do incidente conforme a criticidade declarada do ativo.
SEVERITY_BY_CRITICALITY = {
    "CRITICAL": "HIGH",
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "LOW": "LOW",
}
DEFAULT_SEVERITY = "MEDIUM"

#: Domínio usado quando TAG_DATA_DOMAIN não está preenchida. Precisa existir
#: como rota de fallback no hub, senão o alerta não tem destino.
DEFAULT_DOMAIN = "DEFAULT"


# ---------------------------------------------------------------------------
# Códigos de erro publicados no hub
# ---------------------------------------------------------------------------

SOURCE_FRESHNESS = "FRESHNESS"
SOURCE_VOLUME = "VOLUME"

ERROR_CODE_STALE = "ASSET_STALE"
ERROR_CODE_VOLUME = "VOLUME_OUT_OF_RANGE"
ERROR_CODE_EMPTY = "EMPTY_LOAD"


# ---------------------------------------------------------------------------
# Classificação de falhas do Spark/Unity Catalog
# ---------------------------------------------------------------------------

#: Fuso assumido quando `spark.sql.session.timeZone` não responde um nome de
#: fuso que o Python reconheça.
#:
#: O PySpark entrega TIMESTAMP como `datetime` SEM tzinfo: o instante está
#: correto, mas o fuso fica implícito nessa configuração da sessão. Em
#: cluster Databricks ela é UTC por padrão, e é esse o valor assumido se a
#: leitura falhar — com aviso no log, porque assumir fuso errado desloca
#: toda medição de atraso sem nenhum sintoma visível.
DEFAULT_SESSION_TIMEZONE = "UTC"

#: Valores de `spark.sql.session.timeZone` que significam UTC.
#:
#: Resolvidos sem consultar a base de fusos do sistema (`zoneinfo`), que
#: depende do pacote `tzdata` e NAO existe numa instalacao Windows limpa —
#: ali `ZoneInfo("UTC")` levanta ZoneInfoNotFoundError. Em cluster Databricks
#: a base existe, mas o caminho mais comum (sessao em UTC) nao deve depender
#: dela: o job precisa rodar igual na maquina de quem desenvolve.
NOMES_DE_UTC = frozenset({"UTC", "Z", "GMT", "UCT", "UNIVERSAL", "ZULU",
                          "ETC/UTC", "ETC/GMT", "ETC/UCT", "+00:00", "00:00"})

#: Código de erro de tabela/view que não existe no catálogo.
#:
#: É o caso ESPERADO no bootstrap de um ambiente novo: as tabelas de destino
#: só nascem no primeiro write do job. Ver _tabela_ausente().
ERRO_TABELA_AUSENTE = "TABLE_OR_VIEW_NOT_FOUND"

#: Código de erro de tabela externa órfã: existe registro no Unity Catalog,
#: mas o caminho em storage não existe mais ou não é Delta.
#:
#: Diferente de ERRO_TABELA_AUSENTE, este NÃO é esperado e não se resolve
#: sozinho: é dado quebrado no lake (LOCATION errado, diretório apagado sem
#: dropar a tabela, ou formato não Delta registrado como Delta). O job não
#: tem como observar o ativo, então o conta como cobertura perdida e segue.
ERRO_PATH_AUSENTE = "DELTA_PATH_DOES_NOT_EXIST"

#: Extrai o caminho de storage da mensagem de ERRO_PATH_AUSENTE.
#:
#: O caminho é o dado mais útil para quem vai corrigir — ele mostra na hora
#: quando o LOCATION aponta para o lugar errado (uma tabela `afr_ft_glitem_ksb1`
#: apontando para `.../afr_ft_ksb1`, por exemplo). Sem ele, o operador tem de
#: abrir a mensagem inteira do Spark, com o plano de execução no meio.
PATH_AUSENTE_RE = re.compile(r"(abfss://\S+?)\s+doesn't exist")


#: Valor gravado nas métricas de um ativo que o job TENTOU ler e não conseguiu.
#:
#: A linha existe em tbl_dbr_it_asset_observability para registrar a
#: tentativa: sem ela, um ativo ilegível simplesmente não aparecia na
#: execução, e "não foi observado" ficava indistinguível de "não existe mais"
#: para quem consulta a tabela — só o log contava a história, e log não se
#: consulta em SQL.
#:
#: O marcador que distingue essa linha de uma observação de verdade é
#: `updated_at IS NULL`: nenhuma tabela Delta lida com sucesso devolve
#: lastModified nulo no DESCRIBE DETAIL. Toda análise sobre a tabela que
#: queira só leituras reais filtra por `updated_at IS NOT NULL` — é o que
#: read_previous_observations() faz para não usar a linha de tentativa como
#: referência de volume.
METRICA_NAO_LIDA = 0
