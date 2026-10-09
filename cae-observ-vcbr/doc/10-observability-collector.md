# 10 — Job `observability-collector`

Caminho crítico de alerta. A cada 15 minutos descobre o que falhou, terminou, sumiu ou travou no Databricks e no ADF, transforma cada observação em evento ([02](02-resolucao-de-eventos.md)), detecta execuções esperadas que não aconteceram, envia as notificações pendentes ao Teams e registra um sinal de vida do ciclo.

| Item | Valor |
|---|---|
| Tipo | Azure Container Apps Job, agendado |
| Código | [src/observability-collector/](../src/observability-collector/) (pacote `collector/`, entrada `python -m collector`) |
| Infra / esteira | `infra/main.bicep` + `infra/params.json`; `azure-pipelines.yml` sobre [src/templates/test-build-push-deploy.yml](../src/templates/test-build-push-deploy.yml) |
| Cadência | `*/15 * * * *`, `parallelism: 1`, timeout de réplica 600 s, 1 retentativa |
| Identidade | `mi-observ-coletor-{dev-,}vcbr` |

## Entradas e saídas

| De / Para | O quê | Mecanismo |
|---|---|---|
| Hub (lê e grava) | `tbl_cfg_workspaces`, `tbl_ctl_watermark`, `tbl_active_executions`, `tbl_executions`, `tbl_ctl_reconciliation_gaps`, `tbl_cfg_execution_expectations`, `vw_pending_notifications`; grava incidentes via `resolve_event()` e `sent_at`/`attempt_count`/`last_error` na outbox | `pyodbc`, token Entra da MI |
| Databricks, cada workspace ativo | `jobs/runs/list` (`active_only`, `completed_only`) e `runs/get` (API 2.2) | HTTPS, MI |
| Log Analytics | `ADFActivityRun` e `DatabricksJobs` | Logs Query API (KQL), MI |
| Key Vault | URL do webhook do Teams (`kv://<nome>`) | REST, MI |
| Teams | `MessageCard` no Incoming Webhook da rota | HTTPS |
| Log Analytics | `ADFPipelineRun` e `ADFTriggerRun` | Logs Query API (KQL), MI |
| Azure Monitor | Métrica de nós disponíveis do Self-Hosted IR, por ADF cadastrado | API de métricas (`Microsoft.Insights/metrics`), MI |
| Log Analytics | `Observabilidade_Heartbeat_CL`, um registro por ciclo | Logs Ingestion API (DCE + DCR) |
| Código de saída | `1` se qualquer bloco falhou, `0` caso contrário (alimenta o alerta de métrica do job) | |

## Fluxo de processamento

Cada bloco trata o próprio erro: falha de um workspace ou origem é contada e o ciclo segue.

1. **Workspaces.** Lê `tbl_cfg_workspaces WHERE active = 1`.
2. **Databricks, por workspace** ([databricks.py](../src/observability-collector/collector/databricks.py)): (a) pagina `runs/list?active_only=true`, remove de `tbl_active_executions` o que sumiu e faz `MERGE` do que veio; (b) para cada run que sumiu, `runs/get` e trata como concluído; (c) varre concluídos desde `min(watermark DBX:<ws>, menor start_time aberto) − 15 min`; (d) para cada concluído resolve a identidade (abaixo), grava `tbl_executions` com `observed_at = start_time` e, se a linha for nova, o estado for terminal e houver identidade, chama `resolve_event`: `PASS` para `SUCCESS`, `FAIL` para o resto, exceto `CANCELED` (só registra); (e) avança `DBX:<ws>`.
3. **ADF** ([adf.py](../src/observability-collector/collector/adf.py)), se `LOG_ANALYTICS_WORKSPACE_ID` definido: consulta `ADFActivityRun` (`Succeeded`/`Failed`, deduplicada por `ActivityRunId`) com `TimeGenerated > watermark − 15 min`; grava `tbl_executions` e chama `resolve_event` com a chave estável `<factory>/<pipeline>/<atividade>`, `error_code = 'ADF_ACTIVITY_FAILED'` nas falhas e o `ActivityRunId` em `correlation_ref` (ver [02](02-resolucao-de-eventos.md)). Avança `ADF:PipelineRun`.
   **ADF — pipeline run e trigger run** ([adf_runs.py](../src/observability-collector/collector/adf_runs.py)): ver [Pipeline, trigger e carência](#pipeline-trigger-e-carência). **ADF — Self-Hosted IR** ([integration_runtime.py](../src/observability-collector/collector/integration_runtime.py)): ver [Disponibilidade de nós do Self-Hosted IR](#disponibilidade-de-nós-do-self-hosted-ir); roda fora do bloco do Log Analytics, porque usa a API de métricas. Os três blocos têm tratamento de erro próprio e contam em `adf_events` do heartbeat (o heartbeat tem um indicador por fonte, não por tipo de sinal).
4. **Rede de apoio** ([reconciliation.py](../src/observability-collector/collector/reconciliation.py)): compara `DatabricksJobs` (`runSucceeded`, `runFailed`, `runTriggered`) com o que já é conhecido. Run ausente vira candidato em `tbl_ctl_reconciliation_gaps`; ausente de novo no ciclo seguinte, é gravado em `tbl_executions` com `capture_source = 'LOG_ANALYTICS'`, `status = 'UNKNOWN'`, **sem** chamar `resolve_event`.
5. **Ausência e job travado** ([expectations.py](../src/observability-collector/collector/expectations.py)), sobre `tbl_cfg_execution_expectations` ativa. *Ausência:* para `TIME_OF_DAY` e `MONTHLY`, calcula a última ocorrência esperada no fuso da linha (respeita `days_of_week`, `day_of_month`/`last_day_of_month` e feriado nacional de [holidays.py](../src/observability-collector/collector/holidays.py) quando `observes_national_holidays = 0`); passou da ocorrência + `tolerance_minutes` sem execução observada → `FAIL` com `JOB_NOT_EXECUTED`. `INTERVAL` mede contra a última execução (sem nenhuma, não avalia). *Travado:* run em `tbl_active_executions` acima de `max_expected_duration_min` → `FAIL` com `JOB_STUCK`. O avaliador **nunca fecha** incidente; o fechamento vem da observação normal na mesma correlação.
6. **Notificação** ([notification.py](../src/observability-collector/collector/notification.py)), se `KEY_VAULT_URI` definido: lê `vw_pending_notifications`; só o canal `teams` é implementado. Resolve `kv://<nome>` (cache no ciclo), envia o `MessageCard` e só então marca `sent_at`; falha incrementa `attempt_count` e grava `last_error`.
7. **Heartbeat** ([heartbeat.py](../src/observability-collector/collector/heartbeat.py)), sempre por último: `Status` (`OK`/`ERROR`), duração, contagens do ciclo e `ErrorsCount`. Sem DCE/DCR configurados, só loga aviso.

## Pipeline, trigger e carência

Complementa a atividade: `ADFActivityRun` não vê pipeline que falha sem executar atividade (erro de parâmetro, de configuração, de permissão) nem trigger que falha ao disparar.

| Sinal | Regra | Chave do incidente |
|---|---|---|
| `ADFPipelineRun` `Failed` | Só abre incidente se **nenhuma atividade da mesma execução falhou** (senão o incidente de atividade já explica). `FAIL`, `error_code = ADF_PIPELINE_FAILED` | `<factory>/<pipeline>/pipeline` |
| `ADFPipelineRun` `Succeeded` | `PASS`: fecha o incidente de pipeline aberto | idem |
| `ADFPipelineRun` `Cancelled` | Só registrado em `tbl_executions`, sem incidente (mesma regra do Databricks) | — |
| `ADFTriggerRun` `Failed` / `Succeeded` | `FAIL` (`ADF_TRIGGER_FAILED`) / `PASS` | `<factory>/trigger/<nome>` |

A chave é **estável** (por pipeline/trigger, não por execução), por isso o `PASS` da execução seguinte fecha o incidente — o que o incidente de atividade (`adf:<activity_run_id>`) não consegue (D08, [02](02-resolucao-de-eventos.md)). O nome do Data Factory sai do `_ResourceId` (nenhuma das duas tabelas tem coluna para ele). Os watermarks (`ADF:PipelineRunStatus`, `ADF:TriggerRun`) avançam sobre `ingestion_time()`, não sobre `TimeGenerated`.

### A carência de ingestão

**Problema.** Uma execução de pipeline gera várias linhas no Log Analytics: uma em `ADFPipelineRun` e uma por atividade em `ADFActivityRun`. Elas não chegam juntas. Se a linha do pipeline chega antes das atividades, o coletor vê "pipeline falhou e nenhuma atividade falhou" e abre um incidente de pipeline indevido; quando a atividade chega, abre também o de atividade — o mesmo problema com dois alertas, justamente o que a regra quer evitar.

```
10:00  pipeline carga-vendas falha porque a atividade copy falhou
10:01  linha de ADFPipelineRun (Failed) chega ao Log Analytics
10:03  linha de ADFActivityRun (copy, Failed) ainda NÃO chegou
10:05  coletor roda sem carência: pipeline falho, nenhuma atividade falha -> incidente de pipeline INDEVIDO
10:06  atividade chega -> incidente de atividade (duplicado)
```

**Solução.** Pipeline `Failed` só é avaliado quando o `End` dele é mais antigo que `PIPELINE_FAILURE_GRACE_MINUTES` ([constants.py](../src/observability-collector/collector/constants.py), hoje 10 min). No exemplo, o coletor só avalia o pipeline às 10:10, já com a atividade no Log Analytics, e não abre o incidente de pipeline.

**Regras de detalhe.**
- Só pipeline `Failed` espera. `Succeeded` e `Cancelled` são avaliados na hora.
- Se um `Failed` está esperando, as execuções **posteriores do mesmo pipeline** também esperam: avaliar um `Succeeded` mais novo antes do `Failed` mais velho deixaria o incidente aberto depois de a situação já ter voltado ao normal.
- O watermark não passa da linha que está esperando; ela continua dentro da janela do ciclo seguinte (sobreposição de `OVERLAP_MINUTES`).
- Pipeline que esperou ainda não foi registrado em `tbl_executions`; é registrado quando avaliado.

**Custo.** O alerta de "pipeline falhou sem atividade falha" chega até a carência mais tarde. O alerta de atividade, que cobre a maioria dos casos, **não espera**. Dez minutos é uma estimativa: a documentação do Log Analytics fala em até ~15 min de latência de ingestão, sem garantia. Calibrar medindo a latência real (`ingestion_time()` menos `TimeGenerated`) em produção; se a latência passar da carência, o sintoma é incidente de pipeline duplicado de uma atividade que falhou.

## Disponibilidade de nós do Self-Hosted IR

Monitora **só** se o nó caiu. CPU, memória, fila e atraso de pickup não são avaliados (sobrecarga é fora de escopo).

- **Cadastro.** `tbl_cfg_integration_runtimes` ([01](01-hub-azure-sql.md)): ADF dono do SHIR, nome do IR, assinatura, resource group e `expected_nodes`. Mantida pela API de configuração (repositório próprio). Linked IR não se cadastra: o sinal vem do ADF dono. O IR que não está na tabela não é monitorado; o número esperado de nós nunca é fixo no código.
- **Fonte: a API de métricas do Azure Monitor** (`Microsoft.Insights/metrics` sobre o ADF), métrica `IntegrationRuntimeAvailableNodeNumber`, agregação `Minimum`, intervalo `PT1M`, com `$filter=IntegrationRuntimeName eq '*'`, que divide a série pela dimensão do IR. Uma chamada por Data Factory cobre todos os IRs dele. Token `management.azure.com`, o mesmo escopo que o `sync-expectations` já usa para ler triggers do ADF. Ver [Por que não pelo Log Analytics](#por-que-não-pelo-log-analytics).
- **Estados.** `OK` (nós disponíveis maior ou igual ao esperado), `PARTIAL` (menos que o esperado, `MEDIUM`), `DOWN` (nenhum nó, `HIGH`), `NO_SIGNAL` (nenhuma medição recente, `HIGH`).
- **Um incidente por (ADF, IR)**, `natural_key = <factory>/ir/<nome>`, `error_code = IR_NODES_UNAVAILABLE` (ou `IR_NO_SIGNAL`). `FAIL` na degradação, `PASS` quando o número esperado volta, o que fecha o incidente.
- **Eventos por mudança de estado, não por medição.** O estado de partida vem do banco, pelo `error_code` do incidente aberto: `NO_SIGNAL` se o incidente é de silêncio, degradado se é de indisponibilidade, `OK` se não há incidente. Nó a menos durante vários ciclos é um único alerta no Teams.
- **Sem watermark.** A janela é fixa (`IR_LOOKBACK_MINUTES`, hoje 60 min) e cobre mais de um ciclo do job, para que um ciclo perdido não deixe transição de fora. Reprocessar medição já vista é inofensivo: o `source_event_id` de cada uma é determinístico e o reenvio vira `DUPLICADO`.
- **Mensagem para quem opera:** "Self-Hosted IR 'IR-VCBR-01' (ADF DF-X-VCBR): 1 de 2 nós indisponível desde 14:32" (hora em `DISPLAY_TIMEZONE`). Quando a leitura apenas continua um incidente já aberto, o texto diz "última medição às HH:MM" em vez de "desde", porque o início real não foi visto nesta janela.
- **Falha de consulta não é "sem sinal".** Um ADF que não responde faz o ciclo terminar com erro (e heartbeat `ERROR`), sem abrir incidente: o problema é do coletor, e tratá-lo como silêncio culparia o IR errado. Os outros ADFs continuam sendo avaliados.

### Sem sinal é estado, não desvio

IR cadastrado que fica `IR_NO_SIGNAL_MINUTES` (hoje 10) sem nenhuma medição é tratado como parado: `FAIL`, severidade `HIGH`, `error_code = IR_NO_SIGNAL`, com a mensagem "sem nenhuma medição de nós há mais de 10 minutos. O IR pode estar parado ou o diagnóstico do ADF foi desligado." Não há como distinguir as duas hipóteses pela métrica, e nos dois casos quem opera precisa agir.

Ser um **estado da mesma máquina**, e não um desvio à parte, é o que faz a piora aparecer:

```
14:32  1 de 2 nós                -> abre incidente MEDIUM, avisa no Teams
15:00  o IR fica mudo
15:15  PARTIAL -> NO_SIGNAL      -> novo evento, severidade sobe para HIGH
15:30  segue mudo                -> NO_SIGNAL -> NO_SIGNAL, nada é emitido
16:00  medições voltam com 2 nós -> PASS, fecha o incidente
```

Como a métrica vem da API, e não de ingestão no Log Analytics, "sem ponto" significa que ela realmente não foi emitida: o prazo só precisa cobrir a granularidade da métrica (1 min) e alguma folga, em vez de absorver latência de ingestão.

### Por que não pelo Log Analytics

Métrica de plataforma exportada por Diagnostic Settings cai em `AzureMetrics`, e essa tabela **não tem coluna de dimensão**: a documentação do Azure Monitor diz que métrica multidimensional é exportada achatada e **agregada entre as dimensões**. O nome do Integration Runtime é uma dimensão (`IntegrationRuntimeName`), então por esse caminho ele não chega ao workspace, e num ADF com dois SHIRs o valor viria agregado — a queda de um nó ficaria diluída pelo outro IR, justamente o que este monitor existe para detectar.

A API de métricas divide a série por dimensão, então o sinal é por IR. Ganhos de tabela:

| | Log Analytics (`AzureMetrics`) | API de métricas |
|---|---|---|
| Nome do IR | perdido | preservado |
| ADF com dois SHIRs | valor agregado, queda diluída | um valor por IR |
| `AllMetrics` no Diagnostic Settings | obrigatório | dispensável |
| Tabela compartilhada com outros times | sim | não se aplica |
| Custo de ingestão | de todas as métricas do ADF | nenhum |
| Latência | ingestão, sem garantia | estado atual |

## Regras de negócio

- **Identidade de execução Databricks** ([natural_key.py](../src/observability-collector/collector/natural_key.py)). Despacha pelo `run_type`, nunca pela presença de `job_id`. `SUBMIT_RUN`: decompõe `run_name` em `ADF_<factory>_<processo>_<uuid>`; casando, a chave é o nome sem UUID (regra `RUN_NAME`); senão o nome inteiro (`RUN_NAME_SEM_ID`). `JOB_RUN`: `<workspace_id>:<job_id>`. `WORKFLOW_RUN`: sem identidade, só registra (`SEM_IDENTIDADE`).
- **Domínio pelo Data Factory.** `resolve_domain()` mapeia factory → domínio por `DOMAIN_BY_DATA_FACTORY` ([constants.py](../src/observability-collector/collector/constants.py)); hoje o resultado não é usado (ver pendências).
- **Idempotência no coletor.** Sem `source_event_id`, a proteção é o `INSERT ... WHERE NOT EXISTS` em `tbl_executions` por `(source, scope, run_id, observed_at)`: só a primeira observação chama `resolve_event`.

## Configuração

| Variável | Obrigatória | Uso |
|---|---|---|
| `AZURE_CLIENT_ID`, `SQL_SERVER_FQDN`, `SQL_DATABASE` | Sim | Identidade e hub |
| `LOG_ANALYTICS_WORKSPACE_ID` | Não (sem ela, só o sinal de IR roda) | **Workspace ID (GUID, "Customer ID")**, não o Resource ID. O sinal de IR não depende dela |
| `KEY_VAULT_URI` | Não (sem ela, passo 6 não roda) | Resolução de segredos |
| `LOG_ANALYTICS_INGESTION_ENDPOINT`, `LOG_ANALYTICS_DCR_IMMUTABLE_ID` | Não (sem eles, sem heartbeat) | Logs Ingestion API |

Constantes ajustáveis em `collector/constants.py`: sobreposição de janela (`OVERLAP_MINUTES = 15`), tamanho de página (100), versão da Jobs API (`2.2`), mapa factory → domínio, nomes das tabelas do Log Analytics, carência do pipeline falho (`PIPELINE_FAILURE_GRACE_MINUTES`), métrica e dimensão do IR, janela e prazo de silêncio do IR (`IR_LOOKBACK_MINUTES`, `IR_NO_SIGNAL_MINUTES`), severidades do IR, fuso das mensagens (`DISPLAY_TIMEZONE`). O `params.json` é único para dev e prod; o GUID do workspace precisa ser passado por ambiente.

## Pré-requisitos e permissões

| Onde | O quê |
|---|---|
| Conta Azure Databricks | Registrar a MI como service principal e adicioná-la a cada workspace cadastrado. `CAN_VIEW` basta para `JOB_RUN`, mas `SUBMIT_RUN` criado pelo ADF pertence a outra identidade e costuma exigir a MI como admin do workspace — validar com `jobs_api_diagnostics.py` |
| Azure SQL | `db_datareader`, `db_datawriter` |
| Log Analytics | Papel `Log Analytics Reader` (não provisionado pelo Terraform hoje) e, se o workspace usa RBAC por tabela, leitura de `ADFPipelineRun` e `ADFTriggerRun` (Task 10758, sob a história 10745) |
| Data Factories | Papel `Monitoring Reader` nos ADFs cadastrados em `tbl_cfg_integration_runtimes` (ou no resource group), para a API de métricas (Task 10758) |
| API de configuração | CRUD de `tbl_cfg_integration_runtimes` (Task 10759, sob a história 10722); o DDL da tabela vem do `hub-schema-init` |
| ADF e Databricks | Diagnostic Settings ativos para o workspace Log Analytics, com as categorias `ActivityRuns`, `PipelineRuns` e `TriggerRuns` (padrão do script). `AllMetrics` **não** é necessário: a métrica do IR vem da API, não do workspace |
| Key Vault | Access Policy `Get`/`List` de segredos e segredos de webhook cadastrados |
| DCE/DCR | Stream `Custom-Observabilidade_Heartbeat_CL` e papel `Monitoring Metrics Publisher` da MI na DCR |
| Rede | VNet integration até o Private Endpoint do SQL; saída HTTPS para Databricks, Log Analytics, Key Vault e Teams |
| Hub | Watermarks semeados ([01](01-hub-azure-sql.md)) |

## Implantação e operação

A esteira roda testes, constrói a imagem uma vez (contexto de build na raiz, porque o Dockerfile copia `src/shared/event_resolution.py`), publica no ACR `cranalyticsvcbr` com a tag `$(Build.BuildId)` e aplica o Bicep em dev e depois em prod. Execução fora de agenda: `az containerapp job start -n observability-collector-dev -g RG-ANALYTICS-DEV-VCBR`. O alerta de "não executou/falhou" deve ser regra de métrica do Container Apps Job; o de "rodou mas não trabalhou", regra sobre `Observabilidade_Heartbeat_CL`. Nenhum dos dois está provisionado.

## Critérios de aceite

Job Databricks que falha gera incidente `OPEN` e mensagem no Teams em até 15 min; a execução seguinte com sucesso fecha e notifica a resolução. Run acima de `max_expected_duration_min` gera `JOB_STUCK`; job esperado que não roda gera `JOB_NOT_EXECUTED` após a tolerância. Ciclo sem erros termina com código 0 e heartbeat `OK`. Pipeline que falha sem nenhuma atividade falha abre incidente de pipeline após a carência; se alguma atividade falhou, só o incidente de atividade existe. Trigger que falha abre incidente e o disparo seguinte com sucesso fecha. IR cadastrado com `expected_nodes = 2` e um nó a menos abre incidente `MEDIUM`; zero nós, `HIGH`; a volta dos dois nós fecha. IR cadastrado sem nenhuma medição por mais de `IR_NO_SIGNAL_MINUTES` abre incidente `HIGH` de "sem sinal", que fecha quando as medições voltam; um incidente de nó a menos que fica sem sinal **sobe** para `HIGH` em vez de ficar calado.

## Pendências conhecidas

| Gravidade | Descrição | Direção |
|---|---|---|
| Crítica | Estágios `deployment` do template de esteira não fazem checkout do repositório; `infra/main.bicep` não existe no agente e o deploy falha | `- checkout: self` como primeiro passo de cada `deploy` |
| Crítica | `reconciliation.run_cycle` termina com log que usa `log_analytics_runs`, variável inexistente (o nome é `job_ids_by_run`): `NameError` em todo ciclo, heartbeat `ERROR` e código 1 sempre | Corrigir o nome e adicionar teste que rode `run_cycle` inteiro com dublês |
| Alta | Incidentes de origem ADF não fecham sozinhos ([02](02-resolucao-de-eventos.md)) | Ver 02 |
| Alta | Domínio calculado e descartado | Passar o domínio para `resolve_event` |
| Alta | Watermarks do ADF e da rede de apoio usam `TimeGenerated` (horário do evento), não o de ingestão; linha atrasada pode ficar para trás e nunca ser lida | Filtrar e avançar por `ingestion_time()` |
| Alta | Watermark não semeado reprocessa todo o histórico | Semear ou limitar a janela inicial (ex.: 24 h) |
| Alta | **Colunas não confirmadas em ambiente real.** Os nomes de coluna de `ADFPipelineRun` e `ADFTriggerRun` vêm da documentação oficial, não de consulta ao workspace; o nome do Data Factory sai de `_ResourceId` | Rodar as consultas de amostra antes de produção (Task 10762) |
| Média | A carência do pipeline falho (10 min) é estimativa | Calibrar com a latência de ingestão medida (Task 10762) |
| Média | O nome da dimensão do IR e o formato da resposta da API de métricas não foram exercitados contra um ADF real | Task 10762 |
| Média | Alertas de auto-observabilidade, DCE/DCR e `Log Analytics Reader` não provisionados | Criar no Terraform de plataforma |
| Baixa | Run longo iniciado antes da janela pode virar lacuna falsa | Comparar por `run_id` sem filtro de data |
| Baixa | `WORKFLOW_RUN` nunca gera incidente nem é anexado ao pai; canal `email` não implementado (notificação fica pendente) | Implementar ou proibir o canal |
