"""Constantes do coletor.

Tudo que a plataforma pode renomear fica aqui.
"""

# ---------------------------------------------------------------------------
# Tipos de execução do Databricks
#
# Distinção medida sobre 62.016 execuções reais: job_id vem preenchido nos
# três tipos, mas só é estável em JOB_RUN. O despacho da chave natural usa
# estes valores, nunca a presença do campo.
# ---------------------------------------------------------------------------

RUN_TYPE_SUBMIT = "SUBMIT_RUN"      # efêmero, tipicamente disparado pelo ADF
RUN_TYPE_JOB = "JOB_RUN"            # job persistente, agendado ou manual
RUN_TYPE_WORKFLOW = "WORKFLOW_RUN"  # execução aninhada, filha de outra


# ---------------------------------------------------------------------------
# Regras de resolução da chave natural
#
# Gravadas em tbl_problems.key_rule, para medir a cobertura de cada uma.
# ---------------------------------------------------------------------------

ADF_PREFIX = "ADF"  # primeiro componente do nome gerado pelo ADF

KEY_RULE_RUN_NAME = "RUN_NAME"                  # prefixo, com Activity Run ID
KEY_RULE_RUN_NAME_NO_ID = "RUN_NAME_SEM_ID"     # nome inteiro, sem correlação
KEY_RULE_JOB_ID = "JOB_ID"                      # workspace + job_id

#: Incidente aberto pelo avaliador de ausência/job travado (§4.4), nunca
#: por uma execução observada diretamente — grava em tbl_problems.key_rule
#: para medir, à parte das regras acima, o quanto vem de "não vi nada"
#: contra o quanto vem de execução observada com falha.
KEY_RULE_EXPECTATION = "EXPECTATION"


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------

#: Versão da Jobs API usada nas chamadas.
JOBS_API_VERSION = "2.2"
RUNS_LIST_PATH = f"/api/{JOBS_API_VERSION}/jobs/runs/list"
RUNS_GET_PATH = f"/api/{JOBS_API_VERSION}/jobs/runs/get"

#: Tamanho de página nas chamadas paginadas de runs/list. 100 é o teto aceito
#: pela API nesta versão.
RUNS_LIST_PAGE_SIZE = 100

#: Margem aplicada para trás na janela de start_time, absorvendo runs que
#: chegam com atraso na borda.
OVERLAP_MINUTES = 15

#: Janela de correlação entre um evento ADF e um evento Databricks.
#: Dimensionada para absorver a latência de ingestão do Log Analytics.
CORRELATION_WINDOW_MINUTES = 45


# ---------------------------------------------------------------------------
# Escopos de token (§6 do documento — Managed Identity, sem segredo)
# ---------------------------------------------------------------------------

#: Resource ID fixo do Azure Databricks no Microsoft Entra. Não é segredo e
#: não varia por tenant: identifica o serviço para o qual o token é emitido.
#: Mesmo valor usado por sync_execution_expectations.py e hub_replication.py.
DATABRICKS_RESOURCE_ID = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"
DATABRICKS_SCOPE = f"{DATABRICKS_RESOURCE_ID}/.default"

SQL_SCOPE = "https://database.windows.net/.default"

#: Logs Query API do Azure Monitor — usada tanto para o sinal primário do
#: ADF (§3.2) quanto para a rede de apoio do Databricks (§3.1c).
LOG_ANALYTICS_SCOPE = "https://api.loganalytics.io/.default"
LOG_ANALYTICS_QUERY_URL = "https://api.loganalytics.io/v1/workspaces/{workspace_id}/query"

#: API de métricas do Azure Monitor — usada para a disponibilidade de nós do
#: Self-Hosted IR. Mesmo escopo que sync_execution_expectations.py já usa para
#: ler os triggers do ADF.
ARM_SCOPE = "https://management.azure.com/.default"
ARM_METRICS_URL = (
    "https://management.azure.com/subscriptions/{subscription_id}"
    "/resourceGroups/{resource_group}/providers/Microsoft.DataFactory"
    "/factories/{data_factory}/providers/Microsoft.Insights/metrics"
)
ARM_METRICS_API_VERSION = "2018-01-01"

#: Key Vault — só para resolver `tbl_cfg_alert_routing.destination`
#: (`kv://<nome-do-segredo>`) em tempo de execução. Não dá para usar a
#: referência nativa de secret do Container Apps aqui porque o nome do
#: segredo é decidido por uma linha de banco, não fixado no deploy.
KEY_VAULT_SCOPE = "https://vault.azure.net/.default"
KEY_VAULT_API_VERSION = "7.4"
KEY_VAULT_DESTINATION_PREFIX = "kv://"


# ---------------------------------------------------------------------------
# Estado de execução — Jobs API 2.2 (`state.life_cycle_state` /
# `state.result_state`). Nomes fixos da plataforma; ficam aqui porque
# constants.py é onde tudo que a plataforma pode renomear mora.
# ---------------------------------------------------------------------------

#: life_cycle_state em que a Jobs API já decidiu o destino final do run.
#: QUEUED, PENDING, RUNNING, TERMINATING, BLOCKED e WAITING_FOR_RETRY não
#: entram: para esses ainda não há result_state que preste para alerta.
TERMINAL_LIFE_CYCLE_STATES = frozenset({"TERMINATED", "SKIPPED", "INTERNAL_ERROR"})

RESULT_STATE_SUCCESS = "SUCCESS"
#: CANCELED fica de fora de propósito: alguém cancelou deliberadamente, o
#: que não é nem sucesso nem falha do processo — vira apenas uma linha em
#: tbl_executions, sem chamar resolve_event().
RESULT_STATE_FAILURE = frozenset({"FAILED", "TIMEDOUT"})
RESULT_STATE_CANCELED = "CANCELED"

#: event_resolution.resolve_event() só aceita estes dois valores em `result`
#: (src/shared/event_resolution.py).
OBSERVATION_PASS = "PASS"
OBSERVATION_FAIL = "FAIL"

#: error_code do avaliador de ausência/job travado (§4.4, expectations.py).
ERROR_CODE_JOB_NOT_EXECUTED = "JOB_NOT_EXECUTED"
ERROR_CODE_JOB_STUCK = "JOB_STUCK"


# ---------------------------------------------------------------------------
# tbl_ref_source — origem do sinal (src/hub-schema-init/schema_definition.py)
# ---------------------------------------------------------------------------

SOURCE_DATABRICKS = "DATABRICKS"
SOURCE_ADF = "ADF"

CAPTURE_SOURCE_API = "API"
CAPTURE_SOURCE_LOG_ANALYTICS = "LOG_ANALYTICS"

#: Severidade default para uma execução que falhou sem que nada mais
#: informe o contrário. resolve_event() já trata severity com este mesmo
#: default; repetido aqui para o coletor não depender do default da
#: procedure ao decidir o que loga.
DEFAULT_SEVERITY = "MEDIUM"


# ---------------------------------------------------------------------------
# Watermarks (tbl_ctl_watermark.source)
# ---------------------------------------------------------------------------

def watermark_key_databricks(workspace_id: str) -> str:
    """Watermark do passo (c) — varredura de concluídos — por workspace."""
    return f"DBX:{workspace_id}"


#: Watermark da rede de apoio (§3.1c) — um só, como o do ADF: a tabela
#: `DatabricksJobs` do Log Analytics carrega `WorkspaceId` como coluna, então
#: uma consulta cobre todos os workspaces de uma vez, e não pode reaproveitar
#: (nem avançar, nem atrasar) o watermark que o passo (c) usa contra a Jobs
#: API — são fontes com latência de ingestão diferentes.
WATERMARK_KEY_DATABRICKS_RECONCILE = "DBX_LA:reconciliation"


#: Watermark do sinal ADF — um só, porque a consulta ao Log Analytics cobre
#: todos os Data Factories de uma vez (§3.2). Nome herdado do comentário
#: original da tabela (schema_definition.py).
WATERMARK_KEY_ADF = "ADF:PipelineRun"

#: Watermarks de pipeline run e trigger run. Ao contrário do de atividade (que
#: usa `TimeGenerated`), estes avançam sobre `ingestion_time()`: o instante em
#: que a linha chegou ao workspace, que não volta no tempo. `TimeGenerated`
#: dessas tabelas é o horário do evento, e uma linha atrasada ficaria para trás
#: do watermark e nunca seria lida.
#:
#: A disponibilidade de nós do Self-Hosted IR não tem watermark: ela vem da API
#: de métricas, que devolve o estado atual (ver IR_LOOKBACK_MINUTES).
WATERMARK_KEY_ADF_PIPELINE = "ADF:PipelineRunStatus"
WATERMARK_KEY_ADF_TRIGGER = "ADF:TriggerRun"

#: Piso usado quando uma fonte nunca coletou antes. Mesmo valor semeado em
#: tbl_ctl_replication (schema_definition.py), para consistência.
EPOCH_WATERMARK = "1900-01-01T00:00:00.000+00:00"


# ---------------------------------------------------------------------------
# Log Analytics — nomes de tabela do Diagnostic Settings (§3.1c, §3.2)
#
# Nomes fixos da plataforma (schema "resource specific" do Diagnostic
# Settings). ADFActivityRun é a fonte do ActivityRunId que casa com o sufixo
# do run_name de um run Databricks disparado pelo ADF — gravado em
# `correlation_ref` para ligar os dois lados da mesma execução.
# ---------------------------------------------------------------------------

TABLE_ADF_ACTIVITY_RUN = "ADFActivityRun"
TABLE_DATABRICKS_JOBS = "DatabricksJobs"

ADF_STATUS_SUCCEEDED = "Succeeded"
ADF_STATUS_FAILED = "Failed"
ADF_STATUS_CANCELLED = "Cancelled"

#: Pipeline run e trigger run (Diagnostic Settings, categorias PipelineRuns e
#: TriggerRuns). Nenhuma das duas traz o nome do Data Factory em coluna: ele
#: sai do `_ResourceId` (segmento FACTORIES/<nome>).
TABLE_ADF_PIPELINE_RUN = "ADFPipelineRun"
TABLE_ADF_TRIGGER_RUN = "ADFTriggerRun"

# ---------------------------------------------------------------------------
# Disponibilidade de nós do Self-Hosted IR (API de métricas do Azure Monitor)
#
# Não passa pelo Log Analytics de propósito: o Diagnostic Settings exporta
# métrica multidimensional ACHATADA e agregada entre as dimensões, então o
# nome do Integration Runtime não chegaria ao workspace e um ADF com dois
# SHIRs devolveria um valor agregado — a queda de um nó ficaria diluída pelo
# outro IR. A API de métricas divide a série por dimensão, então o sinal é por
# IR. De lambuja: não exige `AllMetrics` ligado, não lê tabela compartilhada
# com outros times e não custa ingestão.
# ---------------------------------------------------------------------------

METRIC_IR_AVAILABLE_NODES = "IntegrationRuntimeAvailableNodeNumber"

#: Dimensão que identifica o Integration Runtime dentro da métrica. O valor
#: `eq '*'` no $filter é o que pede a série dividida por ela.
METRIC_DIMENSION_IR_NAME = "IntegrationRuntimeName"

#: Menor valor do intervalo, não a média: o que interessa é o pior momento da
#: janela, e uma queda de um minuto não pode ser diluída pela média.
METRIC_AGGREGATION_MINIMUM = "Minimum"

#: Granularidade pedida à API. A métrica é amostrada a cada minuto.
METRIC_INTERVAL = "PT1M"

#: Janela olhada a cada ciclo. Não há watermark: a máquina de estado parte do
#: incidente aberto no banco e caminha pelas medições da janela, e reprocessar
#: medição já vista é inofensivo porque o `source_event_id` de cada uma é
#: determinístico. Precisa cobrir mais de um ciclo do job (15 min), para que um
#: ciclo perdido não deixe transição nenhuma de fora.
IR_LOOKBACK_MINUTES = 60

#: Pipeline falho só é avaliado quando o `End` dele é mais antigo que isto.
#: A linha de ADFPipelineRun pode chegar ao Log Analytics antes das linhas de
#: ADFActivityRun da mesma execução; sem a carência, um pipeline que falhou
#: POR CAUSA de uma atividade seria visto como "falhou sem atividade falha" e
#: abriria um incidente duplicado. O custo é esse atraso, só para o alerta de
#: pipeline; o alerta de atividade não espera. Latência de ingestão do Log
#: Analytics não tem garantia (a documentação fala em até ~15 min): calibrar
#: com a latência medida em produção.
PIPELINE_FAILURE_GRACE_MINUTES = 10

#: Self-Hosted IR cadastrado que fica este tempo sem NENHUMA medição de nós é
#: tratado como parado (ou com o diagnóstico do ADF desligado): quem está
#: monitorado e para de dar sinal não pode virar silêncio. Como a métrica vem
#: da API, e não de ingestão no Log Analytics, "sem ponto" significa que a
#: métrica realmente não foi emitida: o prazo só precisa cobrir a
#: granularidade da métrica (1 min) e alguma folga.
IR_NO_SIGNAL_MINUTES = 10

#: Fuso das horas que aparecem em mensagem para quem opera a esteira (o Azure
#: Monitor trabalha em UTC).
DISPLAY_TIMEZONE = "America/Sao_Paulo"

#: error_code / key_rule dos eventos de ADF e de Integration Runtime.
ERROR_CODE_ADF_ACTIVITY_FAILED = "ADF_ACTIVITY_FAILED"
ERROR_CODE_ADF_PIPELINE_FAILED = "ADF_PIPELINE_FAILED"
ERROR_CODE_ADF_TRIGGER_FAILED = "ADF_TRIGGER_FAILED"
ERROR_CODE_IR_NODES_UNAVAILABLE = "IR_NODES_UNAVAILABLE"
ERROR_CODE_IR_NO_SIGNAL = "IR_NO_SIGNAL"
KEY_RULE_ADF_ACTIVITY = "ADF_ACTIVITY"
KEY_RULE_ADF_PIPELINE = "ADF_PIPELINE"
KEY_RULE_ADF_TRIGGER = "ADF_TRIGGER"
KEY_RULE_IR_NODES = "IR_NODES"

#: Severidade do IR: sem nenhum nó é parada total; com nó a menos é degradado.
SEVERITY_HIGH = "HIGH"
IR_SEVERITY_NO_NODES = SEVERITY_HIGH
IR_SEVERITY_NO_SIGNAL = SEVERITY_HIGH
IR_SEVERITY_PARTIAL = DEFAULT_SEVERITY

# ---------------------------------------------------------------------------
# Notificação (§9) — payload mínimo do Teams (formato MessageCard, aceito
# por qualquer conector de Incoming Webhook do Teams sem configuração extra).
# ---------------------------------------------------------------------------

NOTIFICATION_TYPE_OPEN = "OPEN"
NOTIFICATION_TYPE_CLOSE = "CLOSE"

TEAMS_COLOR_HIGH = "FF0000"
TEAMS_COLOR_MEDIUM = "FFA500"
TEAMS_COLOR_LOW = "FFFF00"
TEAMS_COLOR_RESOLVED = "00B294"


# ---------------------------------------------------------------------------
# Heartbeat (§12) — sinal de conteúdo gravado ao fim de cada ciclo.
#
# Vai para a tabela custom `Observabilidade_Heartbeat_CL`, via Logs
# Ingestion API (DCE + DCR) — não a Data Collector API clássica, que exige
# chave compartilhada e violaria a exigência de "sem segredo de longa
# duração" do §6. Pré-requisito fora deste repositório: a Data Collection
# Endpoint e a Data Collection Rule, com a Managed Identity do coletor
# autorizada a publicar no stream (role "Monitoring Metrics Publisher").
# ---------------------------------------------------------------------------

HEARTBEAT_MONITOR_SCOPE = "https://monitor.azure.com/.default"
HEARTBEAT_STREAM_NAME = "Custom-Observabilidade_Heartbeat_CL"

HEARTBEAT_STATUS_OK = "OK"
HEARTBEAT_STATUS_ERROR = "ERROR"


# ---------------------------------------------------------------------------
# Data Factory -> domínio de negócio
#
# Há um ADF por área, e o nome do Data Factory vem no run_name. Isso resolve
# o domínio de 65% do volume sem depender de cadastro manual.
#
# Chave sem o sufixo de ambiente: DF-PIOSI-DEV-VCBR e DF-PIOSI-VCBR são a
# mesma área. Edite este mapa quando um ADF novo entrar em operação; o que
# não estiver aqui cai no domínio padrão e é roteado para a rota DEFAULT.
# ---------------------------------------------------------------------------

DOMAIN_BY_DATA_FACTORY = {
    "DF-PIOSI-VCBR": "Industrial",
    "DF-LOGISTICS-VCBR": "Logistics",
    "DF-ENGEMIX-VCBR": "Engemix",
    "DF-COMERCIAL2-VCBR": "Comercial",
    "DF-NEG-COMERCIAL-PRD-VCBR": "Comercial",
    "DF-CONTROL-VCBR": "Controladoria",
    "DF-MANUTENCAO-VCBR": "Manutencao",
    "DF-GENTE-VCBR": "Gente",
    "DF-333OBRA-PRD-VCBR": "Obra",
    "DF-CORPORATIVO-VCBR": "Corporativo",
    "DF-TI-VCBR": "IT",
    "DF-PUBLIC-VCBR": "Corporativo",
}

#: Sufixos que indicam ambiente e não fazem parte da identidade da área.
ENVIRONMENT_SUFFIXES = ("-DEV-VCBR", "-PRD-VCBR", "-VCBR")

DEFAULT_DOMAIN = "DEFAULT"
