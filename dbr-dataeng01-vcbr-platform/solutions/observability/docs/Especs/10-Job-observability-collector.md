# 10 - Azure Container Apps Job - observability-collector

| Item | Valor |
|---|---|
| Tipo de componente | Azure Container Apps Job, agendado |
| Repositório | `cae-observ-vcbr` (criado, ainda vazio) |
| Arquivo desta spec | `solutions/observability/docs/Especs/10-Job-observability-collector.md` |
| Código | `src/container-apps/observability-collector/` (pacote `collector/`, entrada `python -m collector`) |
| Infra | `infra/main.bicep` + `infra/params.json`; esteira `azure-pipelines.yml` sobre o template `container-apps/templates/test-build-push-deploy.yml` |
| Cadência | A cada 15 minutos (`*/15 * * * *`), `parallelism: 1`, timeout de réplica 600 s, 1 retentativa |
| Identidade | `mi-observ-coletor-{dev-,}vcbr` (atribuída pelo usuário) |
| Especificações relacionadas | [01](01-Hub-Azure-SQL.md), [02](02-Resolucao-de-Eventos.md), [11](11-Job-sync-expectations.md), [50](50-Scripts-Diagnostic-Settings.md) |

## 1. Objetivo funcional

É o caminho crítico de alerta. A cada 15 minutos descobre o que falhou, terminou, sumiu ou travou no Databricks e no ADF, transforma cada observação em evento (especificação 02), detecta execuções que deveriam ter acontecido e não aconteceram, envia as notificações pendentes ao Teams e registra um sinal de vida do próprio ciclo.

## 2. Entradas

| De onde | O quê | Mecanismo | Autenticação |
|---|---|---|---|
| Azure SQL (hub) | `tbl_cfg_workspaces`, `tbl_ctl_watermark`, `tbl_active_executions`, `tbl_executions`, `tbl_ctl_reconciliation_gaps`, `tbl_cfg_execution_expectations`, `tbl_cfg_alert_destinations`, `tbl_cfg_alert_rules`, `vw_pending_notifications` | `pyodbc`, ODBC Driver 18 | Token Entra da MI (`https://database.windows.net/.default`) |
| Databricks, cada workspace ativo | `GET /api/2.2/jobs/runs/list` (`active_only` e `completed_only`), `GET /api/2.2/jobs/runs/get` | HTTPS | Token Entra da MI para o recurso Databricks `2ff814a6-3304-4ab8-85cb-cd0e6f879c1d` |
| Log Analytics | Tabelas `ADFActivityRun` e `DatabricksJobs` | Logs Query API (`api.loganalytics.io/v1/workspaces/{id}/query`), KQL | Token Entra da MI (`https://api.loganalytics.io/.default`) |
| Key Vault | Segredo com a URL do webhook do Teams (`kv://<nome>`) | REST `secrets/{nome}` api-version 7.4 | Token Entra da MI (`https://vault.azure.net/.default`) |

## 3. Saídas

| Para onde | O quê |
|---|---|
| `tbl_active_executions` | Upsert do que está rodando e remoção do que sumiu (por workspace) |
| `tbl_executions` | Uma linha por execução observada (Databricks e ADF), gravada só se ainda não existir |
| `tbl_problems`, `tbl_technical_events`, `tbl_notification_outbox` | Via `resolve_event()` |
| `tbl_ctl_watermark` | Avanço dos três tipos de watermark |
| `tbl_ctl_reconciliation_gaps` | Candidatos a lacuna da rede de apoio |
| `tbl_notification_deliveries` | Uma linha por destino: `sent_at` (sucesso) ou `attempt_count`/`last_error` (falha) |
| `tbl_notification_outbox` | `sent_at` quando todos os destinos receberam |
| Microsoft Teams | Mensagem `MessageCard` no Incoming Webhook de cada destino da regra escolhida |
| Log Analytics, tabela `Observabilidade_Heartbeat_CL` | Um registro por ciclo, pela Logs Ingestion API (DCE + DCR) |
| Código de saída do processo | `1` se qualquer bloco falhou, `0` caso contrário (alimenta o alerta de métrica do Container Apps Job) |

## 4. Fluxo de processamento

Cada bloco tem tratamento de erro próprio: a falha de um workspace ou de uma origem é contada e o ciclo continua.

1. **Conexão e workspaces.** Abre a conexão com o hub e lê `tbl_cfg_workspaces WHERE active = 1`.
2. **Databricks, por workspace** (`databricks.py`):
   1. *Abertos agora.* Pagina `runs/list?active_only=true` (100 por página). Apaga de `tbl_active_executions` os `run_id` do workspace que não vieram e faz `MERGE` dos que vieram. Os que sumiram são os "fechados desde o último ciclo".
   2. *Fechar os que sumiram.* Para cada um, `runs/get?run_id=` e trata como execução concluída (passo 2.4). Erro HTTP em um run gera aviso e segue.
   3. *Varrer concluídos.* Calcula `start_time_from = min(watermark DBX:<ws>, menor start_time ainda aberto) − 15 min` e pagina `runs/list?completed_only=true`. Ignora os já tratados em 2.2.
   4. *Tratar cada execução concluída.* Resolve a identidade (seção 5.1), grava em `tbl_executions` com `observed_at = start_time` e, se a linha for nova, o estado for terminal (`TERMINATED`, `SKIPPED`, `INTERNAL_ERROR`) e houver identidade, chama `resolve_event` com `PASS` para `SUCCESS` e `FAIL` para o resto, exceto `CANCELED`, que só é registrado. `error_code` recebe o `result_state`; `error_description` recebe até 1000 caracteres de `state_message`.
   5. Avança `DBX:<ws>` para o maior `start_time` varrido.
3. **ADF** (`adf.py`), se `LOG_ANALYTICS_WORKSPACE_ID` estiver definido: consulta `ADFActivityRun` com `TimeGenerated > watermark − 15 min`, status `Succeeded` ou `Failed`, deduplicada por `ActivityRunId`. Para cada atividade, grava `tbl_executions` (`scope` = Data Factory, `run_id` = `ActivityRunId`, `natural_key` = `factory/pipeline/atividade`) e, se nova, chama `resolve_event` com `adf_activity_run_id`, `error_code = 'ADF_ACTIVITY_FAILED'` nas falhas e `run_id` = `PipelineRunId`. Avança `ADF:PipelineRun`.
4. **Rede de apoio** (`reconciliation.py`): consulta `DatabricksJobs` (`ActionName` em `runSucceeded`, `runFailed`, `runTriggered`; `RunId` e `JobId` extraídos de `RequestParams`). Compara com o conhecido localmente (`tbl_active_executions` inteira e `tbl_executions` desde o watermark). Um run ausente vira candidato em `tbl_ctl_reconciliation_gaps`; ausente de novo no ciclo seguinte, é gravado em `tbl_executions` com `capture_source = 'LOG_ANALYTICS'` e `status = 'UNKNOWN'`, **sem** chamar `resolve_event`. Candidatos que deixaram de faltar são removidos. Avança `DBX_LA:reconciliation` para o instante atual.
5. **Ausência e job travado** (`expectations.py`), sobre `tbl_cfg_execution_expectations` ativa:
   1. *Ausência.* Para `TIME_OF_DAY` e `MONTHLY`, calcula a última ocorrência esperada no fuso da linha (respeitando `days_of_week`, `day_of_month`/`last_day_of_month` e, se `observes_national_holidays = 0`, pulando feriado nacional de `holidays.py`). Se já passou da ocorrência mais `tolerance_minutes` e a última execução observada em `tbl_executions` (mesma origem, `natural_key` igual ou com prefixo `natural_key/`) é anterior à ocorrência, chama `resolve_event(FAIL, error_code='JOB_NOT_EXECUTED', key_rule='EXPECTATION')`. Para `INTERVAL`, mede contra a última execução observada (sem nenhuma, não avalia).
   2. *Travado.* Junta `tbl_active_executions` com expectativas `DATABRICKS` que tenham `max_expected_duration_min`, por `natural_key = lower(workspace_id + ':' + job_id)`. Quem passou do limite gera `resolve_event(FAIL, error_code='JOB_STUCK')`.
   3. O avaliador nunca fecha incidentes; o fechamento vem da observação normal com a mesma correlação.
6. **Notificação** (`notification.py`), se `KEY_VAULT_URI` estiver definido: carrega os destinos e as regras ativos (`tbl_cfg_alert_destinations` e `tbl_cfg_alert_rules`, uma consulta por ciclo) e lê `vw_pending_notifications`. Para cada notificação, escolhe a regra mais específica que casa com o objeto, o tipo de falha e a severidade do incidente ([especificação 01](01-Hub-Azure-SQL.md), seção 2.6) e envia para cada destino dela, registrando o resultado por destino em `tbl_notification_deliveries`; a outbox só fica com `sent_at` quando todos os destinos receberam. Só o canal `teams` é implementado. Resolve `kv://<nome>` no Key Vault (com cache no ciclo), envia o `MessageCard` (título `[ABERTO|RESOLVIDO] origem — domínio — chave`, cor por severidade) e só então marca a entrega. Falha incrementa `attempt_count` e grava `last_error` daquele destino; a nova tentativa é só para ele. Sem regra que case (instalação sem a regra `* / * / *`), a notificação fica pendente e o erro vai para o log e o heartbeat. O log de cada envio mostra a regra usada.
7. **Heartbeat** (`heartbeat.py`), sempre por último: envia à Logs Ingestion API um registro com `Status` (`OK`/`ERROR`), `DurationSeconds`, `WorkspacesProcessed`, `ActiveExecutions`, `DatabricksEvents`, `AdfEvents`, `ReconciliationGapsFilled`, `NotificationsSent`, `NotificationsFailed`, `OutboxDepth`, `ErrorsCount`, `AbsenceIncidents`, `StuckIncidents`. Sem DCE/DCR configurados, só registra aviso.
8. Encerra com código 1 se algum bloco falhou.

## 5. Regras de negócio

### 5.1 Identidade de execução Databricks (`natural_key.py`)

O despacho é pelo `run_type`, nunca pela presença de `job_id`. `SUBMIT_RUN`: decompõe `run_name` na gramática `ADF_<factory>_<processo>_<uuid>`; se casar, `natural_key` é o nome sem o UUID (minúsculo), `activity_run_id` é o UUID e a regra é `RUN_NAME`; se não casar, a chave é o nome inteiro e a regra `RUN_NAME_SEM_ID`. `JOB_RUN`: chave `<workspace_id>:<job_id>`, regra `JOB_ID`. `WORKFLOW_RUN`: sem identidade; a execução é registrada com `key_rule = 'SEM_IDENTIDADE'` e não gera incidente. Tipo desconhecido usa o `run_name` se houver.

### 5.2 Domínio pelo Data Factory

`resolve_domain()` mapeia o Data Factory extraído do `run_name` para um domínio por `DOMAIN_BY_DATA_FACTORY` em `constants.py`, ignorando sufixo de ambiente.

### 5.3 Idempotência dentro do coletor

Como os eventos do Databricks e do ADF não têm `source_event_id`, a única proteção contra contar a mesma falha duas vezes é o `INSERT ... WHERE NOT EXISTS` em `tbl_executions` pela chave `(source, scope, run_id, observed_at)`: só a primeira observação de uma execução chama `resolve_event`.

## 6. Configuração

| Variável | Origem | Obrigatória | Uso |
|---|---|---|---|
| `AZURE_CLIENT_ID` | Bicep (client ID da MI) | Sim | Seleciona a identidade |
| `SQL_SERVER_FQDN`, `SQL_DATABASE` | Bicep, por ambiente | Sim | Conexão ao hub |
| `LOG_ANALYTICS_WORKSPACE_ID` | `params.json` | Não (sem ela, passos 3 e 4 não rodam) | **Workspace ID (GUID do workspace, "Customer ID")**, não o Resource ID |
| `KEY_VAULT_URI` | Bicep (do Key Vault do ambiente) | Não (sem ela, passo 6 não roda) | Resolução de segredos |
| `LOG_ANALYTICS_INGESTION_ENDPOINT`, `LOG_ANALYTICS_DCR_IMMUTABLE_ID` | `params.json` | Não (sem eles, sem heartbeat) | Logs Ingestion API |

Constantes ajustáveis em `collector/constants.py`: sobreposição de janela (`OVERLAP_MINUTES = 15`), página da Jobs API (100), versão da Jobs API (`2.2`), mapa Data Factory → domínio, nomes das tabelas do Log Analytics.

`logAnalyticsWorkspaceId` recebe o GUID do workspace de cada ambiente (por parâmetro da esteira ou um `params.<ambiente>.json`).

## 7. Pré-requisitos e permissões

| Onde | O que conceder | Quem faz |
|---|---|---|
| Conta Azure Databricks | Registrar a MI como service principal (Application ID = client ID da MI) e adicioná-la a cada workspace de `tbl_cfg_workspaces`. `CAN_VIEW` nos jobs basta para `JOB_RUN`, mas os `SUBMIT_RUN` criados pelo ADF pertencem a outra identidade e, em geral, só ficam visíveis para o dono e para admins; validar com `src/tools/jobs_api_diagnostics.py` e, se necessário, tornar a MI admin do workspace | Admin da conta/workspace Databricks |
| Azure SQL | `db_datareader` e `db_datawriter` (especificação 01) | Admin Entra do servidor |
| Log Analytics | Papel `Log Analytics Reader` no workspace | Owner do workspace |
| ADF e Databricks | Diagnostic Settings ativos para o workspace Log Analytics (scripts da especificação 50) | Plataforma |
| Key Vault | Access Policy `Get`/`List` de segredos (já criada por `terraform/platform/`) e os segredos de webhook cadastrados | Plataforma |
| DCE/DCR | Data Collection Endpoint, Data Collection Rule com o stream `Custom-Observabilidade_Heartbeat_CL` e a tabela custom, e o papel `Monitoring Metrics Publisher` da MI na DCR | Plataforma (fora do Bicep) |
| Rede | Container Apps Environment com VNet integration até o Private Endpoint do Azure SQL e saída HTTPS para Databricks, Log Analytics, Key Vault e webhook do Teams | Rede |
| Hub | Watermarks semeados antes da primeira execução (especificação 01, passo 5) | Implantação |

## 8. Implantação e operação

A esteira roda testes, constrói a imagem uma vez (contexto de build na raiz do repositório, porque o Dockerfile copia `src/shared/event_resolution.py`), publica no ACR `cranalyticsvcbr` com a tag `$(Build.BuildId)` e aplica o Bicep em dev e depois em prod. Para executar fora de agenda: `az containerapp job start -n observability-collector-dev -g RG-ANALYTICS-DEV-VCBR`. Logs ficam no Log Analytics do Container Apps Environment. O alerta de "não executou/falhou" deve ser uma regra de métrica do Container Apps Job e o de "rodou mas não trabalhou", uma regra sobre `Observabilidade_Heartbeat_CL` (§12 do documento principal).

## 9. Critérios de aceite

Com um workspace cadastrado, um job Databricks que falha gera um incidente `OPEN` e uma mensagem no Teams em até 15 minutos, e a execução seguinte com sucesso fecha o incidente e envia a mensagem de resolução. Um job que roda por mais tempo que `max_expected_duration_min` gera `JOB_STUCK`. Um job com expectativa que não roda gera `JOB_NOT_EXECUTED` depois da tolerância. Um ciclo sem erros termina com código 0 e grava heartbeat `OK`.
