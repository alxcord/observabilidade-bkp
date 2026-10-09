Warning: truncated output (original token count: 30006)
Total output lines: 2230

# Desenho Técnico de Referência

## MVP VCBR — ADF, Databricks, Unity Catalog, Power BI e MLOps

| Item | Definição |
|---|---|
| Tipo | Arquitetura e desenho técnico de referência |
| Escopo | MVP VCBR |
| Capacidade | 1,5 FTE |
| Latência analítica | 30–60 minutos |
| Documento relacionado | [Solução conceitual consolidada](solucao-conceitual.md) |

---

## 1. Objetivo

Este documento descreve como implementar o MVP definido na solução conceitual. Ele especifica componentes, fontes de telemetria, fluxos de dados, modelo das Observability Tables, alertas, correlação de execuções, lineage, segurança, CI/CD, testes e critérios de aceite.

O desenho é uma referência para PoC. Nomes finais de recursos, catálogos, schemas, tabelas, identidades e pipelines deverão seguir os padrões corporativos aprovados durante a execução.

## 2. Premissas técnicas

### Geral

1. O escopo inicial será de dez ativos críticos serão escolhidos no início do MVP na VCBR.
2. O escopo Databricks abrangerá os workspaces pertencentes à VC, as tabelas de sistema do databricks incluem tabelas de outras empresas do grupo (VSA, CBA, etc), vamos identificar por uma view corporativa de workspaces já existente que tem essa função.
3. O canal de notificação será o Teams, ServiceNow não será integrado no MVP podendo ser uma evolução natural do produto.
4. Não vamos integrar o Control-M como uma fonte de telemetria, além de não estar sob domínio da VC entendemos que o control M sempre vai disparar ou um ADF ou um Pipeline do databricks, logo, basta olhar os pipelines disparados, além do mais, não vemos problrmas em ser duplamente alertados em casos críticos, podemos receber notificações do Control-M e da observabilidade para o mesmo problema.
5.  As tabelas da solução no Unity Catalog seguirão o padrão corporativo de nomenclatura e serão criadas no namespace de governança correspondente ao ambiente, `dev_governance` ou `prd_governance`.
6.  As bibliotecas corporativas existentes serão usadas para detectar o ambiente e resolver referências `dev`/`prd`; os nomes de ambiente não serão fixados no código nem duplicados em YAML.

### Observabilidade ADF

1. Os ADFs corporativos no `RG-ANALYTICS-VCBR` serão inventariados e habilitados para diagnóstico, Existe um ADF por área; portanto, o domínio pode ser determinado pelo recurso ADF.
2. Será criado um novo Log Analytics no `RG-ANALYTICS-VCBR`, que terá  retenção operacional de 30 dias, ou a configuração mínima equivalente suportada, essa configuração não temos custos de armazenamento.
3. O histórico oficial será persistido em tabelas Delta no Unity Catalog, utilizaremos um schema especififo (dev_governance/prd_governance)
4. Alertas ADF serão emitidos diretamente pelo Azure Monitor, sem esperar a carga das Observability Tables.
5.  O Log Analytics não vai receber nenhum dado além dos logs de processamento do Data Factory, não serão enviados dados de negócio nem registros inválidos, estes devem ser tratados pelos processos de Data Quality.

### Observabilidade databricks

1.  A carga das Observability Tables ocorrerá a cada 30–60 minutos.
2.  Em caso de falha detectada o próprio databricks aciona o logic App

### Lineage

1.  A expectativa é cobrir o lineage do Power BI x Databricks, será coberto somente nos workspaces PowerBI acessíveis à VC, ainda não temos certeza da viabilidade
2.  SAP S/4HANA, BW e Datasphere serão tratados como sistemas externos administrados pelo CoE, não vamos obter o lineage deles, vamos utilizar as tags para criar o lineage do sistema fonte.
3.  As atualizações de lineage não fazem parte do caminho de alerta operacional crítico e não precisam acompanhar o ciclo analítico de 30–60 minutos. Uma ou duas execuções por dia serão suficientes no MVP porém precisamos ser capazes de receber notificações por mudança de schema em tabelas monitoradas.


## 3. Arquitetura lógica

O diagrama citado na versão original não estava versionado junto ao documento e deverá ser recriado a partir da descrição desta seção.

No desenho, o fluxo do Azure Monitor fornece os alertas imediatos do ADF. O fluxo Databricks consulta as Observability Tables em ciclos de 30–60 minutos, aplica as configurações YAML de alertas de execução e Data Quality, registra a decisão e encaminha os alertas elegíveis à mesma Logic App por meio de payload JSON. As System Tables do Unity Catalog também fornecem tags e metadados técnicos utilizados para complementar origem e lineage dos ativos.

## 4. Responsabilidade dos componentes

| Componente | Responsabilidade no MVP |
|---|---|
| Azure Monitor | Métricas, regras e alertas operacionais do ADF |
| Log Analytics | Logs recentes do ADF, consultas KQL e troubleshooting |
| Logic App | Transformação e envio de mensagens ao Teams |
| System Tables | Jobs, tasks, pipelines, lineage e outros sinais Databricks |
| `DESCRIBE DETAIL` | Última modificação registrada no Delta log, tamanho físico e quantidade de arquivos por tabela |
| Unity Catalog | Inventário, tags, constraints, ownership e lineage nativo |
| Observability Tables | Histórico integrado, correlação, scores e consumo analítico |
| Power BI Premium | Dashboard executivo e, quando adequado, operacional |
| Repositório YAML + CI/CD | Fonte única das configurações; validação do conjunto, deploy das regras ADF no Azure Monitor e sincronização da Databricks Git Folder a partir do mesmo commit |
| Processamento periódico Databricks | Avaliação das regras YAML, correlação, supressão, registro da decisão e envio dos alertas elegíveis à Logic App |

## 5. Coleta do Azure Data Factory

### 5.1 Escopo

Todos os ADFs corporativos encontrados no `RG-ANALYTICS-VCBR` deverão ser inventariados. O inventário registrará:

- subscription;
- resource group;
- resource ID;
- nome do ADF;
- domínio/área;
- ambiente;
- responsável técnico;
- status de Diagnostic Settings;
- volume observado;
- data de inclusão.

O domínio será associado ao recurso ADF. Não será inferido por partes do nome do pipeline.

### 5.2 Diagnostic Settings

Categorias iniciais:

- PipelineRun;
- ActivityRun;
- TriggerRun.

As três categorias são relevantes porque:

- PipelineRun fornece o evento operacional principal;
- ActivityRun permite diagnóstico e correlação com Databricks;
- TriggerRun identifica problemas de disparo e agenda.

Fizemos uma estimativa dos sinais do nosso ADF de produção do corporativo e tivemos  registrou, em 31 dias:

| Categoria | Eventos | Volume mensal estimado |
|---|---:|---:|
| PipelineRun | 71.881 | 0,35 GB |
| ActivityRun | 530.247 | 4,11 GB |
| TriggerRun | 18.976 | 0,06 GB |

Foram inventariados 49 ADFs, dos quais 20 apresentaram execução no período. ActivityRun é a principal fonte de volume, mas seu valor diagnóstico justifica a inclusão inicial. Os tamanhos por evento usados na estimativa são premissas e deverão ser medidos no novo workspace.

### 5.3 Fluxo

```text
ADF Diagnostic Settings
        │
        ▼
Log Analytics
        ├── Azure Monitor Alerts
        ├── KQL operacional
        └── coleta incremental 30–60 min
                         │
                         ▼
                  Delta / Observability Tables
```

### 5.4 Alertas operacionais

O desenho deve preferir alertas métricos quando o sinal atender ao caso de uso e Scheduled Query Rules quando forem necessários filtros ou contexto dos logs.

Exemplos:

- `PipelineFailedRuns > 0`;
- `TriggerFailedRuns > 0`;
- pipeline com duração acima do limite;
- atividade com falha;
- ausência de execução esperada, quando houver calendário confiável.

O Azure Monitor acionará um Action Group. O Action Group chamará uma Logic App, que formatará a mensagem e a enviará ao Teams.

## 6. Coleta do Databricks

### 6.1 Fonte principal

Execuções Databricks não serão copiadas integralmente para o Log Analytics. A fonte principal será o catálogo `system`, com destaque para:

- `system.lakeflow.jobs`;
- `system.lakeflow.job_run_timeline`;
- `system.lakeflow.job_task_run_timeline`;
- `system.lakeflow.job_tasks`;
- `system.lakeflow.pipelines`;
- `system.lakeflow.pipeline_update_timeline`;
- `system.access.table_lineage`;
- `system.access.column_lineage`;
- tabelas disponíveis de MLflow, serving, billing, audit e Data Quality Monitoring, conforme habilitação.

O coletor não manterá uma lista fixa de workspaces. O escopo será obtido da view corporativa que identifica os workspaces pertencentes à VC:

- desenvolvimento: `dev_it.corp_curated.vw_vc_workspaces`;
- produção: `prd_it.corp_curated.vw_vc_workspaces`.

As bibliotecas corporativas existentes detectarão o ambiente de execução e resolverão automaticamente o prefixo `dev_it` ou `prd_it`. Portanto, o código utilizará uma referência lógica à view e não armazenará essa escolha em YAML.

Os dados das System Tables serão relacionados à view pelo identificador estável do workspace. O comportamento esperado é:

- workspace incluído na view passa a ser coletado automaticamente;
- workspace removido deixa de produzir novos dados para o escopo VC, sem apagar seu histórico;
- workspace não encontrado na view é excluído do processamento e registrado para diagnóstico;
- indisponibilidade da view interrompe a coleta de forma controlada, evitando processar workspaces fora do escopo.

Se o volume de todos os workspaces VC não representar esforço ou custo relevante, todos serão incluídos desde o MVP. Um recorte menor poderá ser usado apenas para teste inicial, sem se tornar limitação estrutural da solução.

### 6.2 Frequência

- Microbatch a cada 30–60 minutos.
- Janela de sobreposição para absorver atrasos de publicação das System Tables.
- `MERGE` idempotente por chaves naturais de job, run e task.
- Watermark persistido e reprocessamento controlado.
- Coleta, freshness, correlação e avaliação de alertas serão tasks do mesmo workflow, evitando inicializações adicionais de compute a cada ciclo.
- A consulta de freshness usará `DESCRIBE DETAIL` somente para os ativos monitorados. O benchmark inicial processou 181 tabelas em 41,33 segundos, com zero erros e compute já ativo; para orçamento, será reservada uma janela de um minuto por ciclo, mantendo o tempo de inicialização do job como parcela separada.

### 6.3 Logs detalhados

Logs de cluster, `stdout`, `stderr` e payloads extensos serão consultados sob demanda. Não serão enviados continuamente ao Log Analytics no MVP.
**REVISAR** Não faz parte do plano olhar logs de cluster. Qual benefício?

## 7. Correlação ADF → Databricks

### 7.1 Comportamento observado

Quando um Databricks Job é executado por uma atividade do ADF, a integração entre as plataformas preenche automaticamente o `run_name` da execução Databricks com um valor que termina no `Activity Run ID` do ADF.

Exemplo observado:

```text
ADF_DF-COMERCIAL2-VCBR_VLOZ_Raw2Cleansed_777efcb7-7256-4cae-9a4c-b9bebe8602a5
```

O único componente interpretado será o UUID final:

```text
777efcb7-7256-4cae-9a4c-b9bebe8602a5
```

Não se deve tentar decompor genericamente Data Factory, pipeline e atividade com base em underscores, espaços ou outros separadores.

### 7.2 Extração

Expressão de referência:

```regex
(?i)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$
```

Exemplo SQL:

```sql
regexp_extract(
  run_name,
  '(?i)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$',
  1
) AS candidate_adf_activity_run_id
```

### 7.3 Join

```text
Databricks run_name
        │
        └── UUID final
                │
                ▼
ADF ActivityRun.ActivityRunId
                │
                ├── PipelineRunId
                ├── ActivityName
                ├── ActivityType
                ├── Status
                └── timestamps
```

O campo extraído deverá ser confirmado contra os dados ADF. Comparações serão normalizadas para minúsculas.

### 7.4 Resultado da correlação

Campos recomendados:

```text
is_triggered_by_adf
adf_activity_run_id
adf_pipeline_run_id
correlation_status
correlation_method
correlation_timestamp
```

Valores possíveis para `correlation_status`:

- `matched`;
- `uuid_detected_not_found`;
- `not_adf_pattern`;
- `pending_adf_arrival`;
- `ambiguous`.

### 7.5 Testes

Validar:

- retries;
- reruns;
- jobs multitarefa;
- atividades ADF diferentes;
- múltiplos ADFs;
- execução manual do mesmo job;
- execução por agendamento Databricks;
- diferença de tempo de chegada entre ADF e System Tables.

Meta inicial: correlacionar ao menos 95% das execuções Databricks iniciadas pelo ADF e elegíveis ao comportamento observado.

Um `correlation_id` explícito não será exigido no MVP. Ele permanece como fallback futuro caso a cobertura seja insuficiente.

## 8. Eventos, notificações e incidentes

Evento técnico, notificação e incidente são entidades distintas.

### 8.1 Regra ADF–Databricks

Quando uma execução Databricks correlacionada ao ADF falhar:

- a falha ADF gera a notificação principal;
- a falha Databricks é persistida;
- job e task aparecem no dashboard;
- a notificação Databricks é suprimida;
- indicadores corporativos contam um incidente agregado, não dois incidentes independentes.

Exemplo:

```text
Incidente agregado
├── ADF PipelineRun: Failed
├── ADF ActivityRun: Failed
└── Databricks JobRun: Failed
    └── Databricks TaskRun: Failed
```

Campos de controle:

```text
notification_eligible
notification_suppressed
suppression_reason
parent_activity_run_id
incident_group_id
```

### 8.2 Execuções exclusivamente Databricks

Execuções manuais, agendadas ou via API, sem correlação ADF, permanecem elegíveis para notificação própria. A decisão ocorre por execução, permitindo que o mesmo Job seja acionado ora pelo ADF, ora diretamente.

No MVP, essa notificação não dependerá da configuração de alertas nativos em cada Job. O mesmo processo Databricks que coleta e consolida os sinais a cada 30–60 minutos deverá:

1. identificar falhas novas nas System Tables;
2. correlacioná-las com execuções ADF;
3. aplicar as regras de elegibilidade e supressão definidas em YAML;
4. registrar o evento e o resultado da avaliação nas Observability Tables;
5. enviar à Logic App, em JSON, somente os alertas elegíveis.

Esse fluxo é periódico e sua latência esperada corresponde ao intervalo da coleta acrescido do tempo de processamento. Ele não deve ser apresentado como alerta imediato.

### 8.3 Control-M

Não haverá supressão entre Control-M e observabilidade. A duplicidade é aceita, pois os mecanismos têm objetivos e equipes operacionais distintos, em outras palavais, aceitaremos ter uma notificação por parte do Control M e outra por parte do sistema de observabilidade do mesmo assunto.

## 9. Observability Tables

### 9.1 Princípios

As Observability Tables serão tabelas Delta registradas no Unity Catalog para armazenar os dados técnicos necessários à monitoração de execuções, Data Quality, freshness, volume, mudanças de schema, lineage, notificações e incidentes. Elas não armazenarão linhas de dados de negócio; registros reprovados, quando precisarem ser preservados, permanecerão em uma área de quarentena própria, e as Observability Tables guardarão somente métricas, contagens e uma referência opcional à quarentena.

Na camada raw será preservado o dado coletado de cada origem com o mínimo de transformação necessário para sua persistência, permitindo auditoria e reprocessamento.

Na camada cleansed haverá um espelho lógico de cada origem, com tipos e estados normalizados, duplicidades eliminadas e segmentos temporais da mesma entidade consolidados, sem cruzamento entre fontes diferentes.

Na camada curated serão realizados cruzamentos, correlações e cálculos para produzir a visão unificada de processos e ativos, incidentes, SLA, freshness, volume, Data Quality, mudanças de schema, lineage e datasets de dashboard.

O processo deverá garantir ingestão idempotente, preservação dos identificadores da origem, rastreabilidade entre as camadas, chaves estáveis para correlacionar execução, processo, ativo, regra, evento e incidente, além de histórico suficiente para tendências e auditoria, inicialmente com retenção de dois anos.

### 9.2 Catálogos, schemas e convenções

Todas as tabelas criadas pelo projeto deverão usar o catálogo de governança correspondente ao ambiente:

- desenvolvimento: `dev_governance`;
- produção: `prd_governance`.

Os schemas corporativos serão:

```text
{dev | prd}_governance.corp_raw
{dev | prd}_governance.corp_cleansed
{dev | prd}_governance.corp_curated
```

A biblioteca corporativa resolverá automaticamente o ambiente. O código trabalhará com uma referência lógica ao catálogo de governança e não concatenará nem manterá `dev_governance`/`prd_governance` em YAML.

As tabelas físicas usarão o prefixo `tbl_`, as views usarão `vw_` e `it` identificará o domínio técnico da plataforma. As origens serão abreviadas como `lga` para Log Analytics, `dbr` para Databricks e `pbi` para Power BI. `observability` e `data_quality` não serão tratados como origens; resultados de Data Quality produzidos por análises executadas no Databricks utilizarão `dbr`. A sigla de origem será usada nas camadas raw e cleansed, enquanto objetos curated que combinem fontes diferentes não terão uma origem artificial no nome. Os nomes serão escritos em `snake_case` e não repetirão a camada, já identificada pelo schema. Os nomes físicos finais permanecem sujeitos à validação da governança antes do deploy.

### 9.3 Objetos espelhados entre raw e cleansed

Os objetos desta seção existirão com o mesmo nome em `corp_raw` e `corp_cleansed`. A versão raw preservará o conteúdo coletado, acrescido apenas dos metadados técnicos da ingestão; a versão cleansed manterá o mesmo conjunto lógico, normalizado, consolidado e deduplicado, sem enriquecimento por outra fonte.

#### 9.3.1 Objetos prioritários

Os objetos prioritários formam o núcleo de coleta necessário para monitorar as execuções ADF e Databricks, calcular seus indicadores básicos de operação, freshness, volume e Data Quality e detectar mudanças estruturais ou de tags nas colunas dos ativos monitorados.

##### `tbl_lga_it_adf_execucao_pipeline`

Armazenará as execuções de pipelines ADF obtidas exclusivamente de `ADFPipelineRun`, no Log Analytics, por consultas KQL incrementais. A raw preservará os registros recebidos e a cleansed manterá uma ocorrência por `RunId`. O conteúdo permitirá identificar qual pipeline executou, como foi iniciado, quando começou e terminou, quanto tempo levou e qual foi seu resultado. Um registro ilustrativo seria:

```text
DataFactory:       adf-prd-vcbr
PipelineName:      PL_CARGA_VENDAS
RunId:             84d472a1-88de-4e7f-aefa-1067d6114539
TriggerName:       TRG_VENDAS_DIARIO
RunStart:          2026-08-27 01:00:03
RunEnd:            2026-08-27 01:18:42
DurationSeconds:   1119
Status:            Failed
ErrorCode:         UserError
Message:           Databricks notebook execution failed
Parameters:        {"reference_date":"2026-08-26"}
```

Os nomes e a disponibilidade dos atributos dependerão do schema efetivamente enviado pelos Diagnostic Settings do ADF. A tabela é necessária para monitorar falhas, duração e SLA dos pipelines.

##### `tbl_lga_it_adf_execucao_atividade`

Armazenará as execuções de atividades ADF obtidas exclusivamente de `ADFActivityRun`, no Log Analytics. A cleansed consolidará uma ocorrência por `Activity Run ID` e tentativa. O conteúdo permitirá identificar qual atividade foi executada dentro do pipeline, seu tipo, sua duração e o erro ocorrido. Um registro ilustrativo seria:

```text
PipelineName:       PL_CARGA_VENDAS
PipelineRunId:      84d472a1-88de-4e7f-aefa-1067d6114539
ActivityName:       NB_RAW_TO_CLEANSED
ActivityRunId:      777efcb7-7256-4cae-9a4c-b9bebe8602a5
ActivityType:       DatabricksNotebook
ActivityRunStart:   2026-08-27 01:05:10
ActivityRunEnd:     2026-08-27 01:18:40
DurationSeconds:    810
Status:             Failed
IntegrationRuntime: AutoResolveIntegrationRuntime
ErrorCode:          3202
Message:            Databricks job failed
```

Entre os tipos de atividade esperados estão `Copy`, `DatabricksNotebook`, `ExecutePipeline`, `Lookup`, `Web`, `StoredProcedure` e `ForEach`. A tabela é necessária para localizar a etapa que falhou e para correlacionar o ADF com o Databricks por meio do `Activity Run ID`.

##### `tbl_lga_it_adf_execucao_gatilho`

Armazenará as execuções de gatilhos obtidas exclusivamente de `ADFTriggerRun`, no Log Analytics. A cleansed manterá uma ocorrência por `Trigger Run ID`. O conteúdo permitirá identificar o gatilho, seu tipo, o horário previsto, o horário efetivo, os pipelines acionados e o resultado. Um registro ilustrativo seria:

```text
DataFactory:        adf-prd-vcbr
TriggerName:        TRG_VENDAS_DIARIO
TriggerRunId:       c06987d4-e894-4c88-8708-2fbb748bd2c7
TriggerType:        ScheduleTrigger
ScheduledTime:      2026-08-27 01:00:00
TriggerTime:        2026-08-27 01:00:01
Status:             Succeeded
TriggeredPipelines: ["PL_CARGA_VENDAS"]
```

Além de gatilhos agendados, poderão existir tipos como `TumblingWindowTrigger` e `BlobEventsTrigger`. A tabela é necessária para distinguir uma falha de acionamento de uma falha ocorrida depois que o pipeline começou.

Campos estruturados como parâmetros, entrada, saída e detalhes de erro poderão estar presentes conforme a configuração dos Diagnostic Settings. Antes de persistir esses conteúdos, deverá ser validado se eles contêm segredos ou dados sensíveis e se precisam ser removidos, reduzidos ou mascarados.

##### `tbl_dbr_it_definicao_job`

Armazenará as versões dos Jobs obtidas exclusivamente de `system.lakeflow.jobs`. A raw preservará os eventos recebidos da origem, enquanto a cleansed será modelada como SCD Tipo 2, usando `account_id`, `workspace_id` e `job_id` como chave de negócio. Cada mudança relevante produzirá uma nova versão, cuja vigência será controlada por `valid_from`, `valid_to` e `is_current`; `change_time` determinará o início da vigência e `delete_time`, quando preenchido, encerrará a última versão sem apagar o histórico. O conteúdo permitirá identificar o nome, o criador, as tags e a configuração vigente do Job em cada período. Um registro cleansed ilustrativo seria:

```text
AccountId:    f05f5725-04c9-4816-9d48-a847bd489baf
WorkspaceId: 6114343631149730
JobId:       288828741352144
JobName:     JOB_CARGA_VENDAS
CreatorId:   svc-data-platform@empresa.com
Tags:        {"domain":"commercial","vc_criticality":"high"}
ValidFrom:   2026-08-15 14:32:10
ValidTo:     null
IsCurrent:   true
```

Essa tabela descreve o Job, mas não suas execuções. Ela é necessária para traduzir os identificadores presentes nas tabelas de execução em Jobs reconhecíveis nos dashboards e incidentes. Os atributos disponíveis seguirão o schema publicado da System Table.

##### `tbl_dbr_it_definicao_tarefa`

Armazenará as versões das tarefas configuradas nos Jobs, obtidas exclusivamente de `system.lakeflow.job_tasks`. A raw preservará os eventos recebidos da origem, enquanto a cleansed será modelada como SCD Tipo 2, usando `account_id`, `workspace_id`, `job_id` e `task_key` como chave de negócio. Cada alteração na tarefa ou em suas dependências produzirá uma nova versão com `valid_from`, `valid_to` e `is_current`; uma exclusão encerrará a versão vigente sem remover as versões anteriores. O conteúdo permitirá reconstruir quais tarefas compunham um Job em determinado momento e quais dependências estavam configuradas. Um registro cleansed ilustrativo seria:

```text
AccountId:      f05f5725-04c9-4816-9d48-a847bd489baf
WorkspaceId:   6114343631149730
JobId:         288828741352144
TaskKey:       RAW_TO_CLEANSED
DependsOnKeys: ["INGEST_RAW"]
ValidFrom:     2026-08-15 14:32:10
ValidTo:       null
IsCurrent:     true
```

Essa tabela permite reconstruir a composição e a ordem lógica das tarefas de um Job. Informações que não forem expostas por `system.lakeflow.job_tasks`, como certos detalhes internos da configuração da tarefa, não serão presumidas nem buscadas por API no modelo inicial.

As execuções serão associadas à versão vigente da definição por uma junção temporal em que o início da execução seja maior ou igual a `valid_from` e menor que `valid_to`. Para a versão atual, `valid_to` permanecerá nulo e será tratado como intervalo aberto. Dessa forma, renomeações, alterações de dependência e exclusões posteriores não modificarão a interpretação das execuções históricas.

##### `tbl_dbr_it_execucao_job`

Armazenará os segmentos temporais de execução obtidos exclusivamente de `system.lakeflow.job_run_timeline`. Como uma execução longa pode ocupar vários registros na origem, a cleansed os agrupará por execução, usando o menor início e o maior término para calcular a duração total, sem misturar novas tentativas ou novas execuções. Um registro consolidado ilustrativo seria:

```text
AccountId:          f05f5725-04c9-4816-9d48-a847bd489baf
WorkspaceId:       6114343631149730
JobId:             288828741352144
RunId:             1061636155463171
RunName:           ADF_PL_CARGA_VENDAS_777efcb7-7256-4cae-9a4c-b9bebe8602a5
TriggerType:       ONETIME
RunType:           SUBMIT_RUN
RunStartTime:      2026-08-27 01:05:12
RunEndTime:        2026-08-27 01:18:38
DurationSeconds:   806
ResultState:       FAILED
TerminationCode:   RUN_EXECUTION_ERROR
ComputeIds:        ["0707-000254-lnhn8a6x"]
IsFinished:        true
TimelineSlices:    1
```

Essa tabela permite monitorar sucesso, falha e duração dos Jobs, identificar como foram acionados e extrair do `run_name` o `Activity Run ID` usado na correlação com o ADF. `DurationSeconds` e `TimelineSlices` serão calculados na cleansed; os demais nomes deverão ser ajustados ao schema efetivamente disponibilizado pela System Table.

##### `tbl_dbr_it_execucao_tarefa`

Armazenará os segmentos temporais de tarefa obtidos exclusivamente de `system.lakeflow.job_task_run_timeline`. A cleansed os consolidará por execução e tentativa, mantendo separadas as tarefas de um Job multitarefa. Um registro consolidado ilustrativo seria:

```text
AccountId:          f05f5725-04c9-4816-9d48-a847bd489baf
WorkspaceId:       6114343631149730
JobId:             288828741352144
JobRunId:          1061636155463171
TaskRunId:         1061636155463172
ParentRunId:       null
TaskKey:           RAW_TO_CLEANSED
TaskStartTime:     2026-08-27 01:07:02
TaskEndTime:       2026-08-27 01:18:35
DurationSeconds:   693
ResultState:       FAILED
TerminationCode:   RUN_EXECUTION_ERROR
ComputeIds:        ["0707-000254-lnhn8a6x"]
TimelineSlices:    1
```

Essa tabela permite localizar qual tarefa causou a falha, diferenciar falhas do Job das falhas de suas etapas e analisar duração e recorrência por tarefa. Os identificadores de execução e os campos derivados serão confirmados durante a especificação física contra o schema disponível da System Table.

##### `tbl_dbr_it_tabela`

Armazenará o inventário de tabelas e views obtido exclusivamente de `system.information_schema.tables`, por coleta periódica de baixa frequência, inicialmente diária. A raw preservará cada snapshot recebido e a cleansed será modelada como SCD Tipo 2, usando catálogo, schema e nome da tabela como chave de negócio e mantendo `valid_from`, `valid_to` e `is_current`. Uma ausência somente encerrará uma versão depois que a extração completa da origem terminar com sucesso, evitando interpretar falhas de coleta como exclusões. Um registro cleansed ilustrativo seria:

```text
CatalogName:      prd_logistica
SchemaName:       corp_cleansed
TableName:        entrega
TableType:        BASE TABLE
DataSourceFormat: DELTA
TableOwner:       grp-data-logistica
Comment:          Entregas consolidadas
Created:          2025-11-10 14:20:00
LastAltered:      2026-08-20 09:15:00
ValidFrom:        2026-08-20 09:15:00
ValidTo:          null
IsCurrent:        true
```

Essa tabela é necessária para identificar os ativos Databricks; tags e colunas permanecerão em tabelas próprias. `LastAltered` representa a última alteração conhecida do objeto ou de seus metadados e não será utilizado como evidência de atualização dos dados nem para calcular freshness.

##### `tbl_dbr_it_tag_tabela`

Armazenará as tags obtidas exclusivamente de `system.information_schema.table_tags`, também por coleta inicialmente diária. Cada registro representará uma tag associada a uma tabela. A cleansed será modelada como SCD Tipo 2, usando catálogo, schema, tabela e nome da tag como chave de negócio, para que criticidade, domínio e SLA históricos possam ser associados às execuções do mesmo período. Um registro cleansed ilustrativo seria:

```text
CatalogName: prd_logistica
SchemaName:  corp_cleansed
TableName:   entrega
TagName:     vc_freshness_sla
TagValue:    4h
ValidFrom:   2026-08-01 00:00:00
ValidTo:     null
IsCurrent:   true
```

Entre os valores esperados estão `vc_data_domain`, `vc_criticality`, `vc_source_system`, `vc_refresh_frequency`, `vc_freshness_sla` e `observability_enabled`. Essa tabela é necessária para enriquecer os ativos e interpretar seus requisitos de monitoração na curated; nenhuma tag será incorporada diretamente em `tbl_dbr_it_tabela`.

##### `tbl_dbr_it_coluna`

Armazenará os snapshots de colunas obtidos exclusivamente de `system.information_schema.columns`, limitados às tabelas monitoradas. A raw preservará o estado recebido em cada coleta e a cleansed será modelada como SCD Tipo 2, usando catálogo, schema, tabela e coluna como chave de negócio. Como a origem representa o estado atual, `valid_from` indicará quando uma versão foi observada pela coleta, e não necessariamente o instante exato da alteração. Um registro cleansed ilustrativo seria:

```text
CatalogName:     prd_logistica
SchemaName:      corp_cleansed
TableName:       entrega
ColumnName:      data_entrega
OrdinalPosition: 8
DataType:        timestamp
IsNullable:      false
ColumnComment:   Data efetiva da entrega
ValidFrom:       2026-08-27 04:00:00
ValidTo:         null
IsCurrent:       true
```

Essa tabela permitirá detectar inclusão e remoção de colunas e mudanças de tipo, nulabilidade, posição ou comentário. Uma coluna ausente somente terá sua versão encerrada depois que a extração completa da origem terminar com sucesso.

##### `tbl_dbr_it_tag_coluna`

Armazenará as tags de coluna obtidas exclusivamente de `system.information_schema.column_tags`, limitadas às tabelas monitoradas. Cada registro representará uma tag associada a uma coluna, e a cleansed será modelada como SCD Tipo 2 usando catálogo, schema, tabela, coluna e nome da tag como chave de negócio. Um registro cleansed ilustrativo seria:

```text
CatalogName: prd_logistica
SchemaName:  corp_cleansed
TableName:   entrega
ColumnName:  cpf_motorista
TagName:     data_semantic_type
TagValue:    cpf
ValidFrom:   2026-08-27 04:00:00
ValidTo:     null
IsCurrent:   true
```

Essa tabela permitirá acompanhar inclusão, remoção e alteração de tags relacionadas, por exemplo, à classificação, sensibilidade e ao tipo semântico da coluna. A ausência de uma tag somente encerrará sua versão depois que a extração completa terminar com sucesso.

##### `tbl_dbr_it_atualizacao_tabela`

Armazenará snapshots técnicos das tabelas Delta explicitamente monitoradas, obtidos por `DESCRIBE DETAIL`. Essa coleta não será executada indiscriminadamente para todas as tabelas do Unity Catalog: o escopo será limitado inicialmente aos ativos críticos ou marcados com `observability_enabled = true`. Cada ciclo registrará `lastModified`, `sizeInBytes`, `numFiles` e `createdAt`, além do horário e do identificador da coleta. A raw preservará as observações recebidas; a cleansed normalizará os atributos e evitará estados repetidos por meio de uma chave determinística formada pelo nome qualificado da tabela e pelo conteúdo técnico observado. Um registro cleansed ilustrativo seria:

```text
CatalogName:    prd_logistica
SchemaName:     corp_cleansed
TableName:      entrega
LastModified:   2026-08-27 03:42:18
SizeInBytes:    1801151177
NumFiles:       15
CreatedAt:      2025-10-08 18:11:12
CollectedAt:    2026-08-27 04:00:00
CollectionRunId: 8a81d58e-55a3-4c98-a248-68df22eb33da
```

`LastModified` representa a última transação registrada no Delta log. Ele pode avançar por operações como `OPTIMIZE`, `VACUUM` ou alteração de propriedades, mesmo sem chegada de novos dados. Como processos de manutenção separados são raros no escopo inicial, o MVP aceitará essa aproximação em favor do menor tempo de execução e da possibilidade de monitorar mais tabelas. Quando houver manutenção separada frequente, divergência relevante ou necessidade de auditoria da operação, o processo consultará `DESCRIBE HISTORY` sob demanda para distinguir `WRITE`, `MERGE`, `UPDATE`, `DELETE` e `COPY INTO` de operações de manutenção; essa consulta não fará parte do ciclo padrão.

O benchmark inicial executado em notebook com compute já ativo materializou o resultado de 181 tabelas em 41,33 segundos, sem erros. Esse valor sustenta uma premissa orçamentária de um minuto por ciclo para a etapa de freshness, mas não inclui inicialização do cluster nem a gravação definitiva nas Observability Tables. O piloto deverá medir essas parcelas separadamente no workflow de produção.

`SizeInBytes` e `NumFiles` permitirão acompanhar a evolução do volume físico por tabela mediante comparação entre estados observados. Essas métricas não representam contagem de registros. Quando a quantidade de linhas for necessária, ela deverá vir das métricas da operação de escrita, da execução do pipeline ou de uma regra específica de Data Quality, sem executar `COUNT(*)` generalizado a cada ciclo. `system.information_schema.tables.last_altered` continuará restrito a alterações do objeto ou de seus metadados e não será usado como evidência de atualização dos dados.

##### `tbl_dbr_it_regra_qualidade`

Armazenará a definição efetivamente carregada pelo executor de Data Quality no Databricks. O YAML ou repositório aprovado continuará sendo a fonte de verdade da configuração; a tabela funcionará como registro de execução e permitirá comprovar qual versão foi utilizada em cada avaliação. A raw receberá a definição resolvida pelo executor, incluindo valores padrão aplicados, e a cleansed manterá uma versão por `quality_rule_id` e `quality_rule_version`, com vigência SCD Tipo 2. A versão será identificada explicitamente no YAML ou calculada por hash determinístico do conteúdo relevante da regra. Um registro cleansed ilustrativo seria:

```text
QualityRuleId:      dq_entrega_data_entrega_not_null
QualityRuleVersion: 7f3a51c2
RuleName:           Data de entrega obrigatória
RuleType:           not_null
RuleScope:          column
AssetName:          prd_logistica.corp_cleansed.entrega
ColumnNames:        ["data_entrega"]
Threshold:          {"max_failure_rate":0.001}
Severity:           high
IsBlocking:         true
Action:             fail
SourceFramework:    custom_sql
Enabled:            true
ValidFrom:          2026-08-20 10:00:00
ValidTo:            null
IsCurrent:          true
```

Essa tabela é necessária para interpretar resultados históricos mesmo depois que uma regra for alterada ou desativada. Alterações de descrição que não mudem o comportamento poderão ser tratadas como metadados; mudanças de expressão, escopo, limite, severidade ou ação deverão gerar uma nova versão.

##### `tbl_dbr_it_resultado_qualidade`

Armazenará cada avaliação de Data Quality executada no Databricks. A raw receberá um envelope com o framework de origem, seu identificador nativo, o contexto da execução e o payload produzido; os adapters descritos na seção 15.4 converterão esse conteúdo para o contrato canônico da cleansed. A chave `quality_result_id` será determinística e garantirá idempotência, e cada resultado fará referência exata a `quality_rule_id` e `quality_rule_version`. Um registro cleansed ilustrativo seria:

```text
QualityResultId:      3b27d6f1...e914
QualityRuleId:        dq_entrega_data_entrega_not_null
QualityRuleVersion:   7f3a51c2
AssetName:            prd_logistica.corp_cleansed.entrega
ColumnNames:          ["data_entrega"]
ExecutionId:          1061636155463171
DatabricksJobRunId:   1061636155463171
DatabricksTaskRunId:  1061636155463172
EvaluatedAt:          2026-08-27 03:42:20
EvaluationStatus:     SUCCESS
QualityOutcome:       FAIL
Severity:             high
RecordsEvaluated:     152000
RecordsPassed:        151810
RecordsFailed:        190
FailureRate:          0.00125
ThresholdApplied:     {"max_failure_rate":0.001}
RuleScore:            0.99875
SourceFramework:      custom_sql
QuarantineReference:  abfss://quarantine/.../execution_id=1061636155463171
```

`evaluation_status` representará a execução técnica da validação, enquanto `quality_outcome` representará a condição dos dados; uma falha técnica não será registrada como reprovação de qualidade. A tabela não armazenará os registros de negócio reprovados, apenas contagens, métricas e uma referência opcional à quarentena. Ela é necessária para alertas, auditoria e cálculo do score de qualidade.

#### 9.3.2 Objetos secundários

Os objetos secundários suportam capacidades que serão implantadas de acordo com o escopo aprovado, como Lakeflow Declarative Pipelines, lineage e integração com o Power BI. Eles não são necessários para iniciar a monitoração operacional de ADF, Jobs Databricks e Data Quality.

##### `tbl_dbr_it_definicao_pipeline`

Condicionada à existência de Lakeflow Declarative Pipelines relevantes, armazenará as versões das definições obtidas exclusivamente de `system.lakeflow.pipelines`. A raw preservará os eventos recebidos e a cleansed será modelada como SCD Tipo 2, usando `account_id`, `workspace_id` e `pipeline_id` como chave de negócio. Essa tabela é necessária para interpretar o identificador presente nas atualizações e preservar o nome vigente do pipeline no momento da execução. Um registro cleansed ilustrativo seria:

```text
AccountId:    f05f5725-04c9-4816-9d48-a847bd489baf
WorkspaceId: 6114343631149730
PipelineId:  1f6c1d2e-218d-4bc2-a9d7-6f02bfd80d76
PipelineName: DLT_VENDAS_CLEANSED
CreatorId:   svc-data-platform@empresa.com
ValidFrom:   2026-08-10 12:15:00
ValidTo:     null
IsCurrent:   true
```

##### `tbl_dbr_it_atualizacao_pipeline`

Condicionada à existência de Lakeflow Declarative Pipelines relevantes, armazenará os segmentos temporais obtidos exclusivamente de `system.lakeflow.pipeline_update_timeline`. A cleansed os consolidará por `account_id`, `workspace_id`, `pipeline_id` e `update_id`, usando o menor início e o maior término para calcular a duração, sem misturar atualizações distintas. Um registro consolidado ilustrativo seria:

```text
AccountId:         f05f5725-04c9-4816-9d48-a847bd489baf
WorkspaceId:      6114343631149730
PipelineId:       1f6c1d2e-218d-4bc2-a9d7-6f02bfd80d76
UpdateId:         6ce2d292-cc23-4c5f-af42-7144a598d26a
UpdateType:       REFRESH
UpdateStartTime:  2026-08-27 02:00:03
UpdateEndTime:    2026-08-27 02:14:51
DurationSeconds:  888
ResultState:      FAILED
TimelineSlices:   1
```

Essa tabela permitirá monitorar sucesso, falha e duração das atualizações Lakeflow. Ela somente será criada em conjunto com `tbl_dbr_it_definicao_pipeline`, quando houver pipelines desse tipo em escopo e um consumidor real para os dados; os nomes dos atributos serão confirmados contra o schema disponibilizado pela System Table.

##### `tbl_dbr_it_lineage_tabela`

Armazenará os eventos de lineage de tabela obtidos exclusivamente de `system.access.table_lineage`. Cada registro representará uma evidência observada de leitura ou escrita entre uma origem e um destino, associada, quando disponível, à entidade que executou a operação. A coleta será incremental, com filtro por `event_date`, watermark em `event_time` e janela de sobreposição para absorver atrasos, sempre limitada aos workspaces VC pela view corporativa. Diferentemente da coleta de freshness por `DESCRIBE DETAIL`, essa coleta será feita por uma consulta centralizada à System Table e não por uma chamada para cada tabela.

A raw preservará os eventos recebidos. A cleansed manterá um evento normalizado e deduplicado, sem agregá-lo a uma relação permanente e sem combiná-lo com Power BI ou origens declaradas. Como a origem não fornece uma chave única para todos os casos, `lineage_event_id` será calculado por hash determinístico dos identificadores da conta, metastore, workspace, entidade e execução, do horário do evento e dos objetos ou paths de origem e destino. Um registro cleansed ilustrativo seria:

```text
LineageEventId:       51a9348d...72fe
AccountId:            f05f5725-04c9-4816-9d48-a847bd489baf
MetastoreId:          a8cd7d6e-5cb8-4af4-baf8-8cb810353012
WorkspaceId:          6114343631149730
EntityType:           JOB
EntityId:             288828741352144
EntityRunId:          1061636155463171
SourceTableFullName:  prd_logistica.corp_raw.entrega
SourcePath:           null
TargetTableFullName:  prd_logistica.corp_cleansed.entrega
TargetPath:           null
CreatedBy:            svc-data-platform@empresa.com
EventTime:            2026-08-27 03:42:18
EventDate:            2026-08-27
```

Os campos de tabela poderão estar vazios quando a origem ou o destino for representado apenas por um path; nesses casos o path será preservado, mas não será convertido automaticamente em uma tabela. Os identificadores de entidade permitirão relacionar o evento, quando possível, a Job, pipeline ou notebook observado. A ausência de um evento não provará que uma relação deixou de existir, pois a cobertura depende do uso de objetos governados pelo Unity Catalog e de operações compatíveis com a captura de lineage.

Na curated, `tbl_it_relacao_lineage` consolidará os eventos em relações navegáveis e manterá informações como primeira e última observação e tipo de evidência. `tbl_it_relacao_ativo_processo` utilizará os identificadores da entidade para relacionar processos aos ativos consumidos ou produzidos. O lineage externo de SAP, BW, Datasphere e Power BI não será gravado nesta tabela Databricks. A cópia local também permitirá preservar o histórico por dois anos após o início da coleta, ainda que a retenção disponível na origem seja menor.

##### `tbl_pbi_it_execucao_scan`

Condicionada à PoC do Power BI, registrará a execução e o resultado de cada coleta realizada pelo fluxo da Scanner API: `workspaces/getInfo`, `scanStatus` e `scanResult`. O grão será um workspace incluído em uma solicitação de scan, identificado por `scan_id` e `workspace_id`. Essa tabela permitirá comprovar se o workspace solicitado foi efetivamente devolvido pela API antes que a ausência de um artefato ou relacionamento seja interpretada como remoção. Um registro cleansed ilustrativo seria:

```text
TenantId:                  72f988bf-86f1-41af-91ab-2d7cd011db47
ScanId:                    d94ac0fd-217a-4a25-a360-2f58e2349681
WorkspaceId:               9e2d2ec7-bd42-40f4-bd5c-12c738ecf07a
RequestedAt:               2026-08-27 02:00:00
CompletedAt:               2026-08-27 02:01:34
ScanStatus:                SUCCEEDED
WorkspaceReturned:         true
LineageRequested:          true
DatasourceDetailsRequested: true
DatasetSchemaRequested:    true
ErrorCode:                 null
```

A tabela é necessária para auditoria, reprocessamento e reconciliação segura. Um scan com falha, incompleto ou sem retorno do workspace não poderá encerrar versões vigentes nas tabelas de artefatos ou lineage.

##### `tbl_pbi_it_metadado_artefato`

Condicionada à mesma PoC, armazenará os artefatos presentes em `scanResult`. A raw preservará um envelope por artefato com `scan_id`, workspace, tipo e payload recebido; a cleansed normalizará os atributos comuns e será modelada como SCD Tipo 2, usando tenant, workspace, tipo e identificador do artefato como chave de negócio. Um único objeto atenderá os tipos retornados pela API, como workspace, modelo semântico, relatório e dataflow, sem tentar colocar todos os atributos específicos em colunas comuns; detalhes próprios de cada tipo poderão permanecer em uma estrutura semiestruturada. Um registro cleansed ilustrativo seria:

```text
TenantId:          72f988bf-86f1-41af-91ab-2d7cd011db47
ScanId:            d94ac0fd-217a-4a25-a360-2f58e2349681
WorkspaceId:       9e2d2ec7-bd42-40f4-bd5c-12c738ecf07a
WorkspaceName:     Logística Corporativa
ArtifactType:      semantic_model
SourceArtifactType: dataset
ArtifactId:        62d98403-2237-4d82-82e0-d5b5b643ac47
ArtifactName:      Indicadores de Entrega
ConfiguredBy:      svc-powerbi@empresa.com
StorageMode:       Import
State:             Active
ObservedAt:        2026-08-27 02:01:34
ValidFrom:         2026-08-27 02:01:34
ValidTo:           null
IsCurrent:         true
```

Snapshots repetidos não gerarão novas versões. Mudanças de nome, workspace, estado ou propriedades relevantes encerrarão a versão anterior. Um artefato ausente somente será desativado depois de um scan completo e bem-sucedido do workspace e após o período de carência definido para a integração, pois indisponibilidade ou perda de permissão não comprovam exclusão. Essa tabela é necessária para representar os consumidores Power BI no inventário corporativo.

##### `tbl_pbi_it_lineage`

Condicionada à mesma PoC, armazenará somente as relações de dependência presentes no `scanResult`. A raw preservará a relação e o contexto do scan que a originou; a cleansed será modelada como SCD Tipo 2 e deduplicará cada aresta por tenant, tipos e identificadores dos artefatos de origem e destino e tipo da relação. Ela não será combinada nessa camada com o lineage Databricks ou com relações SAP declaradas. Um registro cleansed ilustrativo seria:

```text
TenantId:           72f988bf-86f1-41af-91ab-2d7cd011db47
ScanId:             d94ac0fd-217a-4a25-a360-2f58e2349681
SourceWorkspaceId:  9e2d2ec7-bd42-40f4-bd5c-12c738ecf07a
SourceArtifactType: semantic_model
SourceArtifactId:   62d98403-2237-4d82-82e0-d5b5b643ac47
TargetWorkspaceId:  9e2d2ec7-bd42-40f4-bd5c-12c738ecf07a
TargetArtifactType: report
TargetArtifactId:   a199dbb1-1b2e-4510-a81d-2b884836cb37
RelationshipType:  report_uses_semantic_model
ObservedAt:         2026-08-27 02:01:34
ValidFrom:          2026-08-27 02:01:34
ValidTo:            null
IsCurrent:          true
```

Relações internas, como relatório → modelo semântico, serão registradas diretamente quando os dois identificadores estiverem presentes. A ligação modelo semântico → tabela Databricks somente será criada quando os detalhes de datasource e as expressões permitirem resolver inequivocamente o nome governado da tabela; caso contrário, será mantido apenas o datasource ou endpoint informado pelo Power BI, sem inferência por similaridade de nomes. Uma relação ausente somente terá sua versão encerrada após scan completo e período de carência.

A coleta dependerá da habilitação das APIs administrativas de leitura para a identidade de serviço e das permissões exigidas no tenant; pertencer a um grupo administrador de workspaces, isoladamente, não garante acesso à Scanner API. A PoC deverá escolher um único mecanismo oficial de coleta. Se o `lineage-syncer` for aprovado como esse mecanismo, sua saída deverá ser reconciliada com o mesmo modelo e não poderá criar uma segunda fonte concorrente.

### 9.4 Tabelas da camada curated

As tabelas desta seção combinarão dados cleansed, interpretarão configurações ou aplicarão regras da solução. Como esses objetos podem reunir várias fontes, seus nomes não apresentarão `lga`, `dbr` ou `pbi` como se existisse uma única origem.

#### 9.4.1 Objetos prioritários

Os objetos prioritários da curated sustentam o cadastro operacional, a detecção de atrasos, falhas e mudanças em colunas e o controle persistente de eventos, notificações e incidentes. Correlações reproduzíveis diretamente a partir das execuções cleansed serão expostas por view e não duplicadas em uma tabela física.

##### `tbl_it_processo_dado`

Formará o cadastro unificado e versionado dos processos observados, como pipelines ADF, Jobs Databricks, pipelines Lakeflow e, futuramente, Jobs Control-M. Um processo representa uma definição executável, não uma execução específica. A tabela será construída a partir das definições disponíveis e, quando a plataforma não fornecer um inventário, das identidades observadas nas execuções cleansed. `process_id` será uma chave determinística formada pelo sistema, tipo, contexto nativo e identificador nativo do processo. A tabela será SCD Tipo 2 para preservar nome, domínio, criticidade e responsabilidade vigentes em cada período. Um registro ilustrativo seria:

```text
ProcessId:          47d872bd...6f31
System:             dbr
ProcessType:        job
NativeContextId:    6114343631149730
NativeProcessId:    288828741352144
ProcessName:        JOB_CARGA_VENDAS
DiscoveryMethod:    system_definition
Domain:             commercial
Criticality:        high
OwnerTeam:          data-logistica
Enabled:            true
ValidFrom:          2026-08-15 14:32:10
ValidTo:            null
IsCurrent:          true
```

Para Jobs e pipelines Lakeflow, as definições SCD2 do Databricks fornecerão a vigência. Para pipelines ADF, que no modelo inicial não possuem uma tabela própria de definições, o cadastro será criado a partir das execuções observadas e dos metadados aprovados; nesse caso, uma mudança de nome poderá ser interpretada como um novo processo se não houver um identificador nativo estável. Essa tabela é necessária para que expectativas, incidentes e dashboards utilizem uma identidade uniforme sem confundir processo com execução.

##### `tbl_it_ativo_dado`

A parcela Databricks será obtida pelo cruzamento de `corp_cleansed.tbl_dbr_it_tabela` com `corp_cleansed.tbl_dbr_it_tag_tabela`, usando catálogo, schema e nome da tabela. Exemplos iniciais incluem tabelas Delta e views; ativos Power BI e objetos SAP, BW ou Datasphere poderão ser incorporados posteriormente a partir das fontes secundárias aprovadas. É necessária para atribuir uma identidade única aos ativos usados em freshness, qualidade, lineage e Health Score.

##### `tbl_it_expectativa_execucao`

Será obtida pela interpretação de agendas Databricks, gatilhos ADF, tags `vc_refresh_frequency` e `vc_freshness_sla` e configurações aprovadas da solução. Exemplos incluem execução horária, disponibilidade diária até determinado horário e tolerância de atraso. É necessária para detectar ausência de execução e calcular SLA e freshness.

##### `tbl_it_atualizacao_ativo`

Será obtida pelo cruzamento de `corp_cleansed.tbl_dbr_it_atualizacao_tabela` com as expectativas, execuções ADF e Databricks e o cadastro de ativos. Usará `LastModified` como sinal padrão de atualização técnica e as diferenças de `SizeInBytes` e `NumFiles` como evolução do volume físico. Quando houver evidência disponível nas métricas da execução, poderá complementar o registro com processo responsável e quantidade de linhas gravadas. É necessária para calcular freshness e volume por ativo sem realizar varredura dos dados de negócio.

##### `tbl_it_mudanca_coluna`

Será obtida pela comparação temporal das versões SCD Tipo 2 de `corp_cleansed.tbl_dbr_it_coluna` e `corp_cleansed.tbl_dbr_it_tag_coluna`. Cada registro representará uma mudança estrutural ou de tag detectada em uma coluna, classificada, por exemplo, como `column_added`, `column_removed`, `data_type_changed`, `nullability_changed`, `column_tag_added`, `column_tag_removed` ou `column_tag_value_changed`. Um registro ilustrativo seria:

```text
CatalogName:   prd_logistica
SchemaName:    corp_cleansed
TableName:     entrega
ColumnName:    cpf_motorista
ChangeType:    column_tag_value_changed
PreviousValue: sensitive
CurrentValue:  highly_sensitive
DetectedAt:    2026-08-27 04:00:00
Severity:      high
```

Essa tabela permitirá alertar sobre alterações incompatíveis ou críticas e manter uma trilha única para mudanças de estrutura e classificação das colunas. Ela ficará na curated porque contém o resultado de uma comparação e classificação, e não um dado espelhado diretamente da origem.

##### `tbl_it_evento_tecnico`

Será derivada de falhas de execução, violações de SLA, resultados de Data Quality e outras condições alertáveis. Exemplos incluem falha de pipeline, falha de Job, atraso de ativo e reprovação de regra. É necessária para preservar o evento antes das decisões de notificação e agrupamento.

##### `tbl_it_avaliacao_notificacao`

Será obtida pelo cruzamento dos eventos com `vw_it_correlacao_execucao` e com as regras de alerta. Exemplos incluem evento elegível, evento suprimido por estar associado à falha de uma execução upstream, notificação enviada e erro de envio. É necessária para idempotência, supressão de duplicidade e auditoria das notificações.

##### `tbl_it_incidente`

Será produzida pelo agrupamento de eventos que representam o mesmo problema operacional. Uma falha de pipeline ADF, da atividade correspondente e do Job Databricks poderá, por exemplo, formar um único incidente. É necessária para que os indicadores não contem várias vezes a mesma ocorrência.

#### 9.4.2 Objetos secundários

Os objetos secundários da curated complementam o núcleo com análise de impacto e lineage federado. Sua implantação depende da ativação das fontes secundárias correspondentes na raw e na cleansed.

##### `tbl_it_relacao_ativo_processo`

Será derivada do lineage Databricks, das relações Power BI e das relações externas declaradas. Armazenará, por exemplo, que um Job grava uma tabela ou que um modelo semântico consome uma tabela curated. É necessária para análise de impacto e navegação entre execução, processo e ativo.

##### `tbl_it_relacao_lineage`

Consolidará `corp_cleansed.tbl_dbr_it_lineage_tabela`, `corp_cleansed.tbl_pbi_it_lineage` e relações declaradas para SAP, BW ou Datasphere. Exemplos incluem SAP → raw, raw → cleansed e curated → modelo semântico Power BI. Cada relação informará se a evidência é nativa, coletada ou declarada.

### 9.5 Views de consumo

As views somente serão criadas quando oferecerem um conjunto reutilizável por dashboards, alertas ou análises. Elas serão materializadas como tabelas apenas quando volume, custo ou tempo de resposta justificarem a duplicação.

#### 9.5.1 Views prioritárias

As views prioritárias fornecem as correlações reproduzíveis e os dados operacionais necessários aos primeiros dashboards de execução, incidentes, freshness, volume e Data Quality.

##### `vw_it_correlacao_execucao`

Calculará correlações entre execuções concretas de duas plataformas, sem limitar o modelo ao ADF e ao Databricks. Cada linha representará uma relação direcionada entre uma execução upstream e uma execução downstream e informará os sistemas, tipos e identificadores nativos envolvidos, o método, a evidência e o estado da correlação. No caso inicial, a view cruzará `corp_cleansed.tbl_lga_it_adf_execucao_atividade` com `corp_cleansed.tbl_dbr_it_execucao_job`, comparando o UUID final do `run_name` ao `Activity Run ID`. Um registro ilustrativo seria:

```text
CorrelationId:           6c626d35...875b
CorrelationGroupId:      9da67f28...142c
UpstreamSystem:          adf
UpstreamExecutionType:   activity_run
UpstreamExecutionId:     777efcb7-7256-4cae-9a4c-b9bebe8602a5
DownstreamSystem:        dbr
DownstreamExecutionType: job_run
DownstreamExecutionId:   1061636155463171
RelationshipType:       triggers
CorrelationMethod:      run_name_suffix_uuid
CorrelationStatus:      matched
EvidenceValue:          777efcb7-7256-4cae-9a4c-b9bebe8602a5
Confidence:             exact
DetectedAt:             2026-08-27 01:20:00
```

`correlation_id` será calculado deterministicamente a partir das execuções relacionadas, enquanto `correlation_group_id` permitirá reunir uma cadeia com mais de duas plataformas. Uma futura integração Control-M → ADF → Databricks poderá acrescentar novas regras à view por `UNION ALL`, sem mudar seu contrato. Estados como `matched`, `candidate_not_found`, `pending` ou `ambiguous` serão calculados a partir dos dados disponíveis no momento da consulta. Somente correlações `matched` serão utilizadas para criar relações entre processos, agrupar incidentes ou suprimir notificações.

A correlação não será persistida porque pode ser reproduzida a partir das tabelas cleansed. O estado operacional que exige memória permanecerá em `tbl_it_avaliacao_notificacao` e `tbl_it_incidente`. A view somente será materializada se o volume ou o tempo de resposta tornar o cálculo sob demanda inadequado, se forem introduzidas correlações manuais ou se houver necessidade de auditar transições de estado como `pending` → `matched`.

##### `vw_it_saude_processo`

Combinará execuções, cadastro de processos, expectativas e incidentes para apresentar sucesso, falha, duração e SLA. Uma única view atenderá pipelines ADF, Jobs Databricks e pipelines Lakeflow por meio do tipo de processo.

##### `vw_it_incidente`

Combinará incidentes, eventos e avaliações de notificação para apresentar severidade, estado, processos, ativos e evidências relacionadas.

##### `vw_it_freshness`

Combinará expectativa e atualização observada para apresentar horário esperado, horário observado, atraso e estado de freshness de cada ativo.

##### `vw_it_volume`

Usará as métricas de `tbl_it_atualizacao_ativo` e das execuções para apresentar volume processado, variação em relação ao baseline e anomalias identificadas.

##### `vw_it_score_qualidade`

Combinará regras e resultados cleansed para apresentar resultados e score de Data Quality por ativo, regra, domínio e período.

#### 9.5.2 Views secundárias

As views secundárias serão disponibilizadas quando as capacidades de lineage e Health Score estiverem implantadas e possuírem dados suficientes para consumo.

##### `vw_it_cobertura_li…6 tokens truncated…adastro de ativos e as relações de lineage para apresentar cobertura por ativo e tipo de evidência.

##### `vw_it_health_score`

Combinará os indicadores de execução, freshness, volume, Data Quality, schema e lineage para apresentar o Health Score por ativo, produto e domínio.

### 9.6 Objetos não incluídos no modelo mínimo

Não será criada uma cópia de `vw_vc_workspaces` apenas para reproduzir seu conteúdo, pois a view corporativa continuará sendo consultada diretamente para delimitar os workspaces VC. Uma tabela histórica somente será criada se surgir a necessidade formal de auditar alterações nesse pertencimento. O lineage de coluna também não fará parte do modelo mínimo, pois o lineage de tabela atende inicialmente à navegação e à análise de impacto com menor volume e complexidade. Por fim, não serão mantidas views separadas de saúde para pipeline, Job e ativo quando a visão unificada de processos e o Health Score oferecerem o mesmo consumo sem duplicação.



## 10. Tags e metadados

A taxonomia de referência deverá ser consolidada neste documento quando sua fonte original estiver disponível.

### 10.1 Tags relevantes ao MVP

| Tag | Uso |
|---|---|
| `vc_data_domain` | Segmentação e responsabilidade |
| `vc_data_owner` | Responsável de negócio, herdado do schema |
| `vc_data_steward` | Curadoria e qualidade, herdado do schema |
| `vc_source_system` | Sistema principal de origem |
| `vc_criticality` | `low`, `medium`, `high`, `critical` |
| `vc_refresh_frequency` | Frequência interpretável, como `1h` ou `1d` |
| `vc_freshness_sla` | Prazo de disponibilidade |
| `observability_enabled` | Inclusão explícita nas automações |
| `data_semantic_type` | Semântica de coluna para geração de regras |

### 10.2 Origem do dado

Para ativos SAP/BW/Datasphere da PoC, `vc_source_system` e `vc_source_object` devem ser preenchidos quando conhecidos.

### 10.3 Consumo das tags e modelo de suporte

A solução considera a existência das tags e metadados necessários e os utiliza como entrada. A definição de quem cria, altera ou mantém essas tags não faz parte deste desenho técnico.

`vc_data_owner` e `vc_data_steward` continuarão com a semântica definida pela governança corporativa. Eles não devem ser reutilizados para indicar a equipe técnica que atende uma falha.

A identificação de time de suporte é útil, pois diferentes squads podem responder por sistemas, pipelines, produtos, ativos ou regras de qualidade. Entretanto, sua granularidade e seu mecanismo de cadastro ainda precisam ser definidos. A solução deve permitir, sem obrigar neste momento a criação de uma nova tag, associações nos seguintes níveis:

- domínio ou área;
- produto de dados;
- pipeline ou job;
- tabela/ativo;
- regra de qualidade;
- sistema externo de origem.

Quando houver mais de uma associação, uma regra futura de resolução poderá aplicar o responsável mais específico e usar o nível superior como padrão. Para regras de qualidade, a equipe de atendimento poderá fazer parte da configuração da regra, sem necessariamente ser uma tag da tabela.

Até essa definição ser aprovada, o MVP não deverá criar nem pressupor os atributos `source_support_team` ou `source_governance_owner`.

### 10.4 Regras de qualidade

Regras detalhadas não serão armazenadas como tags. O catálogo fornece contexto; o repositório de DQ armazena:

- identificador e nome;
- expressão;
- colunas;
- threshold;
- severidade;
- comportamento `warn`, `drop` ou `fail`;
- owner funcional;
- versão e vigência.

## 11. Alertas como código em YAML

### 11.1 Objetivo

Evitar regras criadas manualmente e sem padrão no portal. O YAML deverá permitir revisão em Git, validação, repetibilidade e separação entre configuração funcional e infraestrutura.

### 11.2 Estrutura sugerida

```text
alerts/
├── context/
│   └── environments.yaml
├── adf/
│   ├── common.yaml
│   ├── logistics.yaml
│   └── people.yaml
├── databricks_execution/
│   ├── common.yaml
│   ├── logistics.yaml
│   └── people.yaml
├── data_quality/
│   ├── logistics.yaml
│   └── people.yaml
└── schema/
    └── alert-rule.schema.json
```

Cada diretório representa uma categoria de sinal e poderá conter vários arquivos por domínio, produto ou conjunto funcional. `common.yaml` conterá somente regras realmente compartilhadas. O pipeline descobrirá recursivamente os arquivos de configuração em `alerts/**/*.yaml`; portanto, não existirá a premissa de um único arquivo por categoria.

A pasta organiza o repositório, mas não substitui os metadados. Cada regra deverá declarar explicitamente campos como `domain` e `owner_team`. Por exemplo, arquivos de Logística e Gente usarão, respectivamente, `domain: logistics` e `domain: people`.

### 11.3 Contexto técnico

```yaml
metadata:
  solution: observabilidade_dados
  environment: prd

azure:
  location: brazilsouth
  log_analytics:
    default_workspace: law_observability_vcbr
    workspaces:
      law_observability_vcbr:
        resource_id: ${LAW_RESOURCE_ID}

  action_groups:
    teams_dados:
      resource_id: ${TEAMS_ACTION_GROUP_ID}

data_factories:
  adf_logistics:
    resource_id: ${ADF_LOGISTICS_RESOURCE_ID}
    vc_data_domain: logistics
    enabled: true

  adf_finance:
    resource_id: ${ADF_FINANCE_RESOURCE_ID}
    vc_data_domain: finance
    enabled: true

defaults:
  evaluation_frequency: PT5M
  window_duration: PT15M
  actions:
    - teams_dados

severity_map:
  critical: 0
  high: 1
  medium: 2
  low: 3
  informational: 4
```

Resource IDs reais deverão ser resolvidos por variáveis seguras do pipeline, não gravados obrigatoriamente nos arquivos funcionais.

### 11.4 Regra genérica

```yaml
alerts:
  - name: adf_pipeline_failed
    display_name: "ADF - pipeline falhou"
    type: metric
    scope: all_enabled_data_factories
    expand_by: data_factory
    enabled: true
    severity: high
    signal: PipelineFailedRuns
    operator: GreaterThan
    threshold: 0
    evaluation_frequency: PT5M
    actions:
      - teams_dados
    tags:
      alert_type: adf_pipeline_failure
```

O gerador expande a regra para cada ADF habilitado e aplica o domínio definido no cadastro do recurso.

### 11.5 Scheduled Query Rule

```yaml
alerts:
  - name: adf_activity_failure_detail
    type: scheduled_query
    target_workspace: law_observability_vcbr
    enabled: true
    severity: high
    evaluation_frequency: PT5M
    window_duration: PT15M
    query: |
      ADFActivityRun
      | where TimeGenerated >= ago(15m)
      | where Status == "Failed"
      | summarize failures = count() by _ResourceId, PipelineName, ActivityName
      | where failures > 0
    actions:
      - teams_dados
```

Filtros de domínio serão baseados em `_ResourceId` e no cadastro de ADFs, não em substrings de nomes de pipeline.

### 11.6 Regra Databricks avaliada pela coleta incremental

```yaml
alerts:
  - name: databricks_execution_failed
    display_name: "Databricks - execução falhou"
    source: databricks
    event_type: job_run
    enabled: true
    severity: high
    condition:
      result_state:
        - FAILED
        - TIMED_OUT
    correlation:
      suppress_when_linked_to_adf: true
    notification:
      logic_app: observability
      channel: teams
```

Esse YAML é configuração funcional lida pelo processo incremental; ele não representa uma Azure Monitor Alert Rule. O processo deverá controlar watermark e idempotência para não reenviar o mesmo alerta em ciclos posteriores. O schema deverá validar fontes, tipos de evento, estados, severidades, critérios de supressão e destinos suportados.

### 11.6 Validações

O pipeline deverá validar:

- sintaxe YAML;
- schema;
- nomes únicos;
- aliases existentes;
- severidade permitida;
- frequência e janela válidas;
- tipo de alerta suportado;
- consulta não vazia;
- recursos dentro do escopo permitido;
- ausência de segredos;
- tags obrigatórias;
- compatibilidade entre sinal, escopo e tipo.

Quando possível, a consulta KQL deverá ser validada contra o workspace antes do deploy.

## 12. CI/CD dos alertas

### 12.1 Fluxo

Um único repositório Git será a fonte das configurações ADF, de execuções Databricks e de Data Quality. Um único pipeline CI/CD validará o conjunto completo e, após a aprovação, executará duas pernas de publicação a partir do mesmo commit.

```text
Pull request
   │
   ├── lint YAML
   ├── valida schema
   ├── resolve aliases
   ├── valida KQL
   ├── gera plano/diff
   └── revisão
          │
          ▼
      aprovação de produção
           │
           ▼
       mesmo commit aprovado
           │
           ├── deploy das regras ADF
           │      → script Python + Azure SDK/IaC
           │      → Azure Monitor Alert Rules
           │
           └── sincronização das regras Databricks e Data Quality
                  → Databricks API/CLI
                  → Databricks Git Folder
```

A Git Folder será vinculada ao mesmo repositório e disponibilizará os YAML no workspace Databricks. O pipeline deverá solicitar sua sincronização com a branch, tag ou, preferencialmente, o commit aprovado. O processamento periódico lerá dessa pasta as configurações de `databricks_execution/` e `data_quality/`. A sincronização não deverá copiar arquivos por um mecanismo paralelo nem produzir uma segunda fonte de verdade.

### 12.2 Requisitos do gerador

- Idempotência.
- Modos `validate`, `plan` e `apply`.
- Criação e atualização determinísticas.
- Relatório de recursos adicionados, alterados e desabilitados.
- Sem exclusão automática de recursos não gerenciados.
- Identificação por tags como `managed_by=observability_alert_pipeline`.
- Autenticação por identidade do pipeline.
- Segredos no Key Vault ou mecanismo corporativo equivalente.
- Logs de deployment sem conteúdo sensível.
- Testes unitários do parser e do mapeamento de severidades.
- Descoberta recursiva e determinística de `alerts/**/*.yaml`.
- Registro do commit implantado no Azure Monitor e sincronizado na Databricks Git Folder.
- Falha explícita quando apenas uma das duas pernas de publicação for concluída.

### 12.3 Exclusão e drift

Uma regra removida do YAML não deve ser apagada silenciosamente em produção. O pipeline deverá primeiro propor sua desativação ou exclusão no plano, sujeita a aprovação.

Recursos alterados manualmente devem aparecer como drift. O comportamento inicial recomendado é falhar o plano e exigir decisão, evitando sobrescrever alterações desconhecidas.

## 13. Teams e Logic App

O Azure Monitor não dependerá do Databricks para notificar falhas ADF.

Fluxo:

```text
Azure Monitor Alert
      → Action Group
      → Logic App
      → Teams
```

A Logic App deverá:

- validar o payload;
- normalizar severidade;
- formatar título e corpo;
- incluir recurso, domínio, pipeline, horário e link operacional;
- registrar falha de entrega;
- não incluir dados sensíveis;
- permitir evolução futura para ServiceNow sem alterar as regras de origem.

O processo incremental Databricks usará a mesma Logic App para notificações periódicas. As regras serão mantidas em YAML; o contrato de integração enviado à Logic App será JSON e deverá incluir, no mínimo, origem, tipo do alerta, severidade, workspace, identificadores da execução, domínio, mensagem, horário de detecção e link operacional.

Fluxo periódico:

```text
System Tables
      → coleta e consolidação Databricks (30–60 min)
      → correlação ADF e avaliação das regras YAML
      → registro do evento e da decisão nas Observability Tables
      → payload JSON para a Logic App
      → Teams
```

## 14. Lineage ponta a ponta

### 14.0 Cadência de atualização

Os fluxos de lineage possuem finalidade de inventário, análise de impacto e investigação, não de detecção operacional imediata. Por isso, a sincronização de lineage interno, externo SAP/BW/Datasphere e Power BI será agendada apenas uma ou duas vezes ao dia. Essa cadência deverá ser independente da coleta operacional de 30–60 minutos e poderá ser reduzida ainda mais caso limites de API, custo ou autorizações externas justifiquem.

Falhas na atualização de lineage deverão ser monitoradas, mas não impedirão a coleta de execuções, a avaliação de alertas nem a atualização dos demais indicadores operacionais.

### 14.1 SAP/BW/Datasphere → ADF/raw

SAP Datasphere é uma plataforma corporativa de integração, modelagem e disponibilização de dados SAP. Para esta solução, seus objetos, assim como objetos do BW e do S/4HANA, são fontes externas upstream das tabelas mantidas pela VC.

A cobertura inicial utilizará os metadados existentes nos ativos Databricks, principalmente:

- `vc_source_system`;
- `vc_source_object`;
- tabela raw de destino.

Será necessário desenvolver um sincronizador de lineage externo. Ele não altera as tags nem os objetos SAP; apenas transforma as declarações existentes em objetos e relações de external lineage no Unity Catalog.

Fluxo:

```text
tags/metadados do ativo Databricks
        │
        ├── vc_source_system
        └── vc_source_object
                  │
                  ▼
normalização e chave estável da origem
                  │
                  ▼
objeto externo SAP/BW/Datasphere
                  │
                  ▼
external lineage: origem → tabela Databricks
```

Processamento esperado:

1. Ler os ativos Databricks incluídos no escopo de observabilidade.
2. Extrair e normalizar `vc_source_system` e `vc_source_object`.
3. Construir uma chave determinística para a origem, por exemplo, sistema + tipo de objeto + nome qualificado.
4. Criar o objeto externo quando ele ainda não existir.
5. Atualizar o objeto quando seus metadados mudarem.
6. Criar a relação upstream → tabela Databricks quando ela ainda não existir.
7. Manter uma marca que identifique objetos e relações administrados por esse sincronizador.
8. Comparar, a cada execução, o estado desejado derivado dos metadados com o estado atualmente materializado.

Regras para evitar duplicidade e resíduos:

- usar `upsert`, nunca inserção cega;
- aplicar a mesma chave estável em todas as execuções;
- não duplicar uma relação já existente;
- remover uma relação gerenciada quando ela deixar de ser declarada;
- remover um objeto externo somente quando ele não possuir mais relações ativas;
- não alterar nem excluir objetos criados manualmente ou por outra integração;
- registrar `first_seen`, `last_seen`, estado e motivo da alteração;
- usar período de carência antes da exclusão definitiva, reduzindo o risco causado por tag temporariamente ausente;
- tratar renomeações de forma controlada para não manter simultaneamente o nome antigo e o novo.

Com apenas as tags, a solução consegue determinar que uma origem deixou de ser declarada pelos ativos VC. Ela não consegue confirmar que o objeto foi fisicamente excluído no Datasphere, BW ou S/4HANA. Essa confirmação exigiria inventário ou API autorizada da plataforma de origem.

Se um ativo possuir múltiplas origens e as tags simples não forem suficientes para representá-las, os relacionamentos adicionais deverão ser mantidos em uma estrutura própria, sem concatenar múltiplos objetos em um único valor de tag.

Os objetos e relações também serão registrados em `corp_cleansed.tbl_observability_it_objeto_externo` e `corp_cleansed.tbl_observability_it_relacao_lineage`, com `evidence_type = declared`, para auditoria do processo de sincronização.

### 14.2 ADF → Databricks

Cobertura correlacionada por execução mediante Activity Run ID extraído do `run_name`. O relacionamento estático pipeline → job poderá ser derivado das execuções observadas, sem exigir a leitura de metadata estática do ADF para a correlação operacional.

### 14.3 Databricks raw → cleansed → curated

Cobertura nativa pelo Unity Catalog e pelas System Tables de lineage, desde que os objetos sejam referenciados por nomes governados e as operações sejam compatíveis com a captura de lineage.

### 14.4 Databricks → Power BI

#### 14.4.1 O que é o `lineage-syncer`

O [`lineage-syncer`](https://github.com/JeffDenzel/lineage-syncer) é um projeto open source publicado no GitHub para sincronizar metadados e lineage do Power BI com o Unity Catalog do Databricks. Ele não é uma funcionalidade nativa da solução da VC e não deve ser pressuposto como conhecido pelo leitor.

Em termos funcionais, a proposta do projeto é:

1. Acessar os workspaces Power BI permitidos à identidade configurada.
2. Inventariar artefatos e dependências disponíveis, como modelos semânticos, relatórios e fontes de dados.
3. Identificar referências a objetos Databricks quando os metadados disponíveis permitirem.
4. Representar os artefatos Power BI como objetos externos no Unity Catalog.
5. Criar relações de external lineage entre tabelas Databricks e seus consumidores Power BI.

O benefício esperado é complementar o lineage nativo do Databricks com a camada de consumo analítico. O projeto será avaliado como acelerador de PoC, não como componente corporativo automaticamente aprovado ou suportado.

#### 14.4.2 Escopo e execução da PoC

Escopo:

- workspaces VC acessíveis pelo grupo administrativo existente;
- modelos semânticos;
- relatórios;
- dataflows, quando aplicável;
- conexões e tabelas de origem disponíveis nas APIs autorizadas.

Passos da PoC:

1. Criar ou selecionar uma conta/aplicação de serviço.
2. Garantir acesso aos workspaces VC pelo processo existente.
3. Testar APIs por workspace.
4. Identificar dependências das Scanner/Admin APIs.
5. Validar as configurações de tenant necessárias com o CoE.
6. Instalar e testar o projeto `lineage-syncer` em ambiente controlado.
7. Registrar modelos e relatórios como ativos externos no Unity Catalog ou nas Observability Tables.
8. Medir cobertura e limitações por modo de conexão.

Antes de produção, avaliar:

- licença;
- manutenção e atividade do projeto;
- dependências e versões suportadas;
- autenticação;
- segurança de segredos;
- idempotência;
- remoção de objetos antigos;
- cobertura de Import, DirectQuery e modelos compostos;
- limites do external lineage;
- suporte operacional;
- necessidade de manter um fork interno ou fixar uma versão aprovada.

A sincronização Power BI deverá seguir o mesmo princípio de reconciliação usado para as origens SAP:

- uma identidade determinística para workspace, modelo semântico e relatório;
- `upsert` de objetos e relações;
- ausência de duplicidade após reprocessamento;
- remoção de relações que deixaram de existir;
- exclusão de objeto somente quando órfão e gerenciado pela integração;
- preservação de objetos manuais ou produzidos por outras integrações;
- distinção entre objeto realmente removido e objeto temporariamente invisível por falha de API ou permissão;
- desativação e período de carência antes da exclusão definitiva;
- trilha de auditoria de inclusões, alterações e remoções.

## 15. Freshness, volume, schema e DQ

### 15.1 Freshness

Fontes:

- tags `vc_refresh_frequency` e `vc_freshness_sla`;
- `DESCRIBE DETAIL`, pelos campos `lastModified`, `sizeInBytes`, `numFiles` e `createdAt`;
- timestamps da execução;
- maior data de negócio, quando definida;
- Data Quality Monitoring, se habilitado.

Algoritmo inicial:

```text
expected_at = regra derivada de vc_refresh_frequency/vc_freshness_sla
observed_at = lastModified do DESCRIBE DETAIL ou timestamp do dado
delay = observed_at - expected_at
```

`DESCRIBE DETAIL` será executado no ciclo de 30–60 minutos apenas para os ativos monitorados e no mesmo workflow da coleta e avaliação de alertas. O benchmark inicial processou e exibiu 181 tabelas em 41,33 segundos, com zero erros e compute previamente ativo. Para orçamento será utilizada a premissa de um minuto por ciclo para essa etapa, enquanto a inicialização do compute permanecerá contabilizada separadamente. O piloto deverá repetir a medição incluindo a escrita na tabela de observabilidade.

O modelo deve distinguir freshness técnica de freshness de negócio. `lastModified` será a evidência padrão de freshness técnica, embora também possa refletir `OPTIMIZE`, `VACUUM` e alterações de propriedades. O MVP aceita essa limitação porque manutenções separadas são raras no escopo observado. `DESCRIBE HISTORY` será usado somente como investigação complementar para ativos em que essa aproximação não seja aceitável.

### 15.2 Volume

Fontes:

- métricas ADF, quando disponíveis;
- `sizeInBytes` e `numFiles` do `DESCRIBE DETAIL`;
- métricas de escrita e contagem de linhas por execução, quando disponíveis;
- DQM completeness, quando habilitado.

O MVP começará com baseline simples por ativo e periodicidade. A evolução física será calculada pela diferença de `sizeInBytes` e `numFiles` entre estados observados. Essas métricas não equivalem à quantidade de registros; contagens de linhas serão usadas somente quando já forem produzidas pelo pipeline ou por uma regra de qualidade, evitando `COUNT(*)` generalizado. Detecção sazonal avançada fica fora do escopo.

### 15.3 Schema

Implementação inicial:

- snapshot de `INFORMATION_SCHEMA`;
- comparação entre versões;
- classificação das mudanças;
- vínculo com consumidores conhecidos;
- registro em `corp_cleansed.tbl_observability_it_mudanca_schema`.

Classificações:

- compatível;
- controlada;
- incompatível;
- crítica.

### 15.4 Data Quality

Para os dez ativos, selecionar um conjunto mínimo de regras com valor claro:

- chave não nula;
- unicidade;
- domínio de valores;
- integridade referencial;
- reconciliação de volume;
- freshness;
- regra de negócio crítica.

Resultados completos ficam no Delta. Somente eventos compactos e alertáveis seriam enviados ao Log Analytics caso essa opção seja aprovada após medir o benefício. O MVP não exige centralizar todos os alertas DQ no Log Analytics.

#### 15.4.1 Separação entre regra e resultado

As definições das regras ficarão em `corp_cleansed.tbl_data_quality_it_regra`. O histórico de avaliações ficará em `corp_cleansed.tbl_data_quality_it_resultado_regra`.

A separação evita repetir expressão, descrição e configuração completa em todas as linhas de resultado, mas preserva a versão da regra efetivamente executada.

Uma linha de resultado representa:

```text
uma regra
+ uma versão da regra
+ um ativo
+ uma avaliação específica
```

#### 15.4.2 Contrato de `tbl_data_quality_it_resultado_regra`

| Campo lógico | Tipo sugerido | Finalidade |
|---|---|---|
| `quality_result_id` | `STRING` | Chave técnica única e determinística do resultado |
| `quality_rule_id` | `STRING` | Identificador estável da regra |
| `quality_rule_version` | `STRING` | Versão executada da regra |
| `asset_id` | `STRING` | Ativo avaliado |
| `rule_scope` | `STRING` | Escopo da regra: tabela, coluna ou conjunto de colunas |
| `column_names` | `ARRAY<STRING>` | Colunas envolvidas, quando aplicável |
| `execution_id` | `STRING` | Identificador principal da execução relacionada |
| `adf_pipeline_run_id` | `STRING` | Pipeline Run ID relacionado, quando houver |
| `adf_activity_run_id` | `STRING` | Activity Run ID relacionado, quando houver |
| `databricks_job_run_id` | `STRING` | Job Run ID relacionado, quando houver |
| `databricks_task_run_id` | `STRING` | Task Run ID relacionado, quando houver |
| `correlation_id` | `STRING` | Identificador de correlação disponível |
| `source_evaluation_id` | `STRING` | Identificador nativo da avaliação, quando fornecido pelo framework |
| `evaluation_started_at` | `TIMESTAMP` | Início da avaliação |
| `evaluated_at` | `TIMESTAMP` | Horário original em que o resultado foi produzido |
| `evaluation_window_start` | `TIMESTAMP` | Início da janela lógica avaliada, quando aplicável |
| `evaluation_window_end` | `TIMESTAMP` | Fim da janela lógica avaliada, quando aplicável |
| `ingested_at` | `TIMESTAMP` | Horário em que o resultado entrou nas Observability Tables |
| `evaluation_status` | `STRING` | Execução técnica: `SUCCESS`, `ERROR` ou `NOT_EXECUTED` |
| `quality_outcome` | `STRING` | Condição do dado: `PASS`, `WARN`, `FAIL` ou `UNKNOWN` |
| `severity` | `STRING` | Criticidade da regra |
| `is_blocking` | `BOOLEAN` | Indica se a violação deve interromper o fluxo |
| `action` | `STRING` | Comportamento configurado: `warn`, `drop` ou `fail` |
| `records_evaluated` | `BIGINT` | Quantidade avaliada |
| `records_passed` | `BIGINT` | Quantidade aprovada |
| `records_failed` | `BIGINT` | Quantidade reprovada |
| `failure_rate` | `DECIMAL` | `records_failed ÷ records_evaluated` |
| `threshold_applied` | `STRING` | Limite aplicado em formato serializável |
| `rule_score` | `DECIMAL` | Score da avaliação, quando aplicável |
| `source_framework` | `STRING` | Expectations, DQM, SQL customizado ou outro adapter |
| `framework_run_id` | `STRING` | Identificador de execução no framework de origem |
| `error_message` | `STRING` | Erro técnico sanitizado |
| `quarantine_reference` | `STRING` | Referência opcional à quarentena, sem copiar registros inválidos |

`evaluation_status = ERROR` significa que a validação não conseguiu executar. `quality_outcome = FAIL` significa que a validação executou corretamente e encontrou dados fora da regra. Esses estados não podem ser combinados em um único campo.

#### 15.4.3 Chave do resultado e idempotência

Não será necessário gerar um identificador numérico sequencial. `quality_result_id` será uma única chave `STRING`, calculada por hash SHA-256 a partir da identidade lógica da avaliação.

Ordem de preferência para identificar a avaliação:

1. `source_evaluation_id`, quando o framework fornecer um identificador estável;
2. `execution_id` combinado com `evaluated_at`;
3. janela de avaliação combinada com o ativo e a regra.

Exemplo de referência:

```sql
sha2(
  concat_ws(
    '||',
    asset_id,
    quality_rule_id,
    quality_rule_version,
    coalesce(source_evaluation_id, execution_id, ''),
    coalesce(cast(evaluation_window_start as string), ''),
    cast(evaluated_at as string)
  ),
  256
) AS quality_result_id
```

Regras:

- `evaluated_at` deve ser o timestamp original da avaliação, normalizado para UTC;
- `ingested_at` não participa da chave;
- reingerir o mesmo evento produz o mesmo ID;
- uma nova avaliação produz um novo ID;
- o writer usa `MERGE` por `quality_result_id`;
- as colunas componentes permanecem armazenadas para consulta e auditoria;
- uma verificação periódica deve confirmar que não existem IDs duplicados.

#### 15.4.4 Adaptadores de resultado

Cada mecanismo de qualidade possui formato próprio. Uma pequena biblioteca compartilhada transformará esses formatos em um contrato canônico antes da persistência.

```text
Lakeflow Expectations ── adapt_expectations()
Data Quality Monitoring ─ adapt_dqm()
Regras SQL próprias ───── adapt_custom_sql()
                              │
                              ▼
                    DataFrame canônico
                              │
                              ▼
                    validate_and_persist()
```

Responsabilidades:

| Componente | Responsabilidade |
|---|---|
| Adapter de origem | Traduzir nomes, estados e métricas do framework para o contrato comum |
| Validador | Verificar campos obrigatórios, tipos e valores permitidos |
| Enriquecedor | Associar contexto de execução, calcular métricas e gerar `quality_result_id` |
| Writer | Executar `MERGE` idempotente na tabela Delta |

Interfaces de referência:

```python
def adapt_expectations(source_df, context) -> DataFrame:
    ...

def adapt_dqm(source_df, context) -> DataFrame:
    ...

def adapt_custom_sql(source_df, context) -> DataFrame:
    ...

def validate_and_persist(results_df) -> None:
    ...
```

O `context` poderá fornecer `asset_id`, IDs de execução, timestamps e demais informações que não estejam presentes no payload de origem.

Estrutura inicial da biblioteca:

```text
quality_observability/
├── schema.py
├── adapters.py
├── validation.py
├── identifiers.py
└── writer.py
```

Os adapters não devem gravar diretamente em tabelas. Todos devem retornar o mesmo schema canônico e utilizar o writer comum. Uma falha de adaptação deve ser registrada como falha técnica do coletor; ela não pode ser convertida silenciosamente em reprovação dos dados.

#### 15.4.5 Registros inválidos

As Observability Tables não armazenarão cópias dos registros de negócio que falharam. Quando o processo precisar manter esses registros, eles deverão permanecer em uma área de quarentena própria e protegida. O resultado de qualidade guardará somente contagens, métricas e uma referência opcional à quarentena.

## 16. Dashboards

### 16.1 Dashboard operacional

Filtros:

- período;
- ambiente;
- domínio;
- ADF;
- workspace;
- pipeline;
- job;
- produto;
- ativo;
- status e severidade.

Páginas:

1. **Operação ADF** — execuções, falhas, triggers, duração e atividades.
2. **Operação Databricks** — jobs, tasks, falhas, duração e origem da execução.
3. **Correlação** — ADF pipeline/activity associados a job/task Databricks.
4. **Saúde dos dados** — freshness, volume, schema e DQ.
5. **Incidentes agregados** — evento principal e evidências técnicas relacionadas.

Uma falha Databricks correlacionada ao ADF deve permanecer visível mesmo quando `notification_suppressed = true`.

### 16.2 Dashboard executivo

- Data Health Score;
- ativos saudáveis, em atenção e críticos;
- cumprimento de SLA;
- incidentes e recorrência;
- MTTD e MTTR quando mensuráveis;
- cobertura de monitoramento;
- cobertura de DQ;
- cobertura de lineage por tipo de evidência;
- evolução por domínio e produto.

O dashboard não deve apresentar lineage declarado como se fosse nativo.

## 17. MLOps

### 17.1 Amostra

Até três workloads:

- MLflow;
- Databricks Model Serving;
- Azure ML.

### 17.2 Métricas mínimas

| Área | Métricas iniciais |
|---|---|
| Treinamento | status, duração, versão, última execução |
| Registro | modelo, versão, estágio/alias, responsável |
| Serving | endpoint, disponibilidade, volume, erro, latência |
| Azure ML | inventário e estado operacional básico |

### 17.3 Fora do MVP

- data drift avançado;
- prediction drift;
- ground truth;
- explicabilidade;
- fairness;
- retraining automático;
- avaliação completa de GenAI.

Se o esforço for maior que o previsto, Azure ML será limitado a inventário ou removido da entrega de 90 dias.

## 18. Segurança e acesso

### 18.1 Azure

- Identidade de deployment com menor privilégio necessário.
- Permissão para criar Diagnostic Settings e Alert Rules nos recursos em escopo.
- Permissão de leitura no Log Analytics para o coletor.
- Logic App com conexões governadas.
- Segredos fora do YAML e do código.

### 18.2 Databricks

- Acesso controlado ao catálogo `system`.
- Acesso à view corporativa `corp_curated.vw_vc_workspaces`, resolvida para `dev_it` ou `prd_it` pela biblioteca de ambiente.
- Filtro obrigatório pelos IDs retornados por essa view.
- Tabelas da solução em `dev_governance` ou `prd_governance`, com o catálogo resolvido pela biblioteca corporativa e grants por função.
- Service principal para jobs de coleta.
- Nenhum token pessoal persistido em código.

### 18.3 Power BI

- Conta/aplicação de serviço aprovada.
- Acesso limitado aos workspaces VC.
- Dependências de configuração do tenant documentadas.
- Armazenamento seguro de credenciais.
- Coleta mínima dos metadados necessários.

### 18.4 Dados sensíveis

Logs e Observability Tables não devem conter:

- linhas completas de dados de negócio;
- valores inválidos sensíveis;
- tokens e segredos;
- parâmetros de conexão;
- SQL com literais sensíveis sem tratamento.

## 19. Observabilidade da própria solução

O MVP deverá monitorar:

- atraso das coletas;
- watermark;
- volume por fonte;
- falhas de ingestão;
- registros rejeitados;
- taxa de correlação;
- custo do Log Analytics;
- latência de notificação;
- falha da Logic App;
- atualização dos dashboards;
- drift entre YAML e alertas implantados.

Indicadores mínimos:

```text
collector_last_success
collector_delay_minutes
source_rows_read
rows_inserted
rows_updated
rows_rejected
correlation_match_rate
notification_delivery_status
```

## 20. Plano de implementação

As atividades abaixo estão em ordem de dependência. A numeração representa a sequência lógica, não significa que todas as atividades precisem esperar a anterior terminar. Os itens explicitamente indicados podem ocorrer em paralelo.

Prioridades:

- **P0 — obrigatória:** necessária para que o MVP funcione.
- **P1 — importante:** necessária para demonstrar os cinco pilares e a visão executiva.
- **P2 — opcional:** executada somente se não comprometer o núcleo do MVP.

### 20.1 Etapa 1 — Preparação e delimitação

| Ordem | Atividade | Dependência | Resultado | Prioridade |
|---:|---|---|---|---|
| 1 | Confirmar patrocinador, equipe de trabalho e participantes das validações | — | Governança do MVP definida | P0 |
| 2 | Selecionar os dez ativos críticos | 1 | Lista formal do piloto | P0 |
| 3 | Identificar para cada ativo o produto, domínio, periodicidade, criticidade e consumidores conhecidos | 2 | Ficha mínima de cada ativo | P0 |
| 4 | Confirmar quais tags e metadados já existem nos ativos selecionados | 2 | Inventário de metadados disponíveis | P0 |
| 5 | Identificar os processos ADF e Databricks que produzem os dez ativos | 2 | Relação ativo → processo | P0 |
| 6 | Definir critérios de aceite e baseline inicial | 2 e 3 | Métricas de sucesso aprovadas | P0 |

As atividades 3, 4 e 5 podem ocorrer em paralelo após a seleção dos ativos.

### 20.2 Etapa 2 — Inventário técnico e acessos

| Ordem | Atividade | Dependência | Resultado | Prioridade |
|---:|---|---|---|---|
| 7 | Inventariar os ADFs do ambiente corporativo VCBR | 1 | Lista de ADFs e identificadores | P0 |
| 8 | Mapear cada ADF ao seu domínio/área | 7 | Cadastro ADF → domínio | P0 |
| 9 | Validar a resolução da view corporativa de workspaces VC em desenvolvimento e produção | 1 | Fonte dinâmica de escopo confirmada | P0 |
| 10 | Validar acesso à view, às System Tables e ao Unity Catalog | 9 | Permissões técnicas confirmadas | P0 |
| 11 | Levantar Diagnostic Settings e alertas já existentes | 7 | Situação atual documentada | P0 |
| 12 | Criar a matriz de acessos da solução | 7 a 10 | Identidades, permissões e pendências | P0 |

As atividades de Azure e Databricks podem ocorrer em paralelo.

### 20.3 Etapa 3 — Fundação operacional do ADF

| Ordem | Atividade | Dependência | Resultado | Prioridade |
|---:|---|---|---|---|
| 13 | Criar o novo workspace Log Analytics | 12 | Destino operacional disponível | P0 |
| 14 | Configurar retenção e controle de acesso | 13 | Workspace protegido e parametrizado | P0 |
| 15 | Habilitar Diagnostic Settings para PipelineRun, ActivityRun e TriggerRun | 11 a 14 | Logs ADF centralizados | P0 |
| 16 | Validar campos, completude, atraso e duplicidade dos logs | 15 | Contrato real das fontes conhecido | P0 |
| 17 | Medir volume ingerido por ADF e categoria | 15 | Baseline real de custo | P0 |
| 18 | Criar Action Group, Logic App e integração com Teams | 13 | Canal de notificação disponível | P0 |
| 19 | Implantar manualmente um alerta controlado para validar o fluxo completo | 15 e 18 | PoC ADF → Teams aprovada | P0 |

O alerta manual da atividade 19 é temporário. Ele valida conectividade e formato antes do desenvolvimento do gerador YAML.

### 20.4 Etapa 4 — Fundação das Observability Tables

| Ordem | Atividade | Dependência | Resultado | Prioridade |
|---:|---|---|---|---|
| 20 | Confirmar o padrão físico das tabelas em `dev_governance`/`prd_governance` e a organização raw, cleansed e curated | 10 e 12 | Estrutura lógica e física disponível | P0 |
| 21 | Validar com a governança os nomes propostos e detalhar campos, tipos, chaves e estados | 3, 5 e 20 | Modelo físico aprovado | P0 |
| 22 | Implementar coleta incremental do Log Analytics | 16 e 21 | Execuções ADF nas Observability Tables | P0 |
| 23 | Implementar coleta das System Tables filtrada dinamicamente pela view de workspaces VC | 9, 10 e 21 | Jobs e tasks dos workspaces VC nas Observability Tables | P0 |
| 24 | Implementar watermarks, janela de sobreposição e `MERGE` idempotente | 22 e 23 | Cargas repetíveis sem duplicidade | P0 |
| 25 | Implantar métricas da própria coleta | 24 | Atrasos e falhas do coletor visíveis | P0 |
| 26 | Agendar o ciclo de atualização de 30–60 minutos | 24 e 25 | Observability Tables atualizadas periodicamente | P0 |

O desenho do modelo pode começar em paralelo à criação do Log Analytics. A implementação da coleta depende da validação dos dados reais.

### 20.5 Etapa 5 — Correlação ADF–Databricks e tratamento de eventos

| Ordem | Atividade | Dependência | Resultado | Prioridade |
|---:|---|---|---|---|
| 27 | Extrair o UUID final do `run_name` Databricks | 23 | Candidato a Activity Run ID | P0 |
| 28 | Correlacionar o UUID ao ActivityRun do ADF | 22 e 27 | Relação ADF → Job Databricks | P0 |
| 29 | Classificar execuções correlacionadas, independentes e pendentes | 28 | Origem de cada execução definida | P0 |
| 30 | Agrupar pipeline, atividade, job e task em um contexto de incidente | 28 e 29 | Eventos técnicos relacionados | P0 |
| 31 | Implementar a regra que suprime somente a segunda notificação Databricks | 29 e 30 | Menos ruído sem perda de evidência | P0 |
| 32 | Testar retries, reruns, jobs multitarefa e execuções independentes | 27 a 31 | Cobertura e exceções documentadas | P0 |
| 33 | Medir a taxa de correlação e comparar com a meta de 95% | 32 | Decisão sobre necessidade de fallback | P0 |

### 20.6 Etapa 6 — Alertas como código

| Ordem | Atividade | Dependência | Resultado | Prioridade |
|---:|---|---|---|---|
| 34 | Definir o schema YAML, o contexto técnico e a estrutura de múltiplos arquivos por categoria e domínio | 8 e 19 | Contrato de configuração dos alertas | P0 |
| 35 | Criar templates comuns para os ADFs | 34 | Regras reutilizáveis | P0 |
| 36 | Implementar validações de schema, aliases, severidade e escopo | 34 | Configurações inválidas bloqueadas | P0 |
| 37 | Implementar o gerador via SDK com modos `validate`, `plan` e `apply` | 35 e 36 | Deployment idempotente | P0 |
| 38 | Criar o pipeline CI/CD, a aprovação e as pernas de deploy Azure Monitor e sincronização Databricks Git Folder | 37 | Publicação controlada do mesmo commit nos dois destinos | P0 |
| 39 | Migrar o alerta temporário, implantar as regras mínimas e validar a leitura dos YAML pelo Databricks | 19 e 38 | Alertas gerenciados pelo repositório YAML | P0 |
| 40 | Executar falhas controladas e validar mensagens no Teams | 39 | Alertas aprovados ponta a ponta | P0 |

O desenvolvimento do YAML pode começar depois da validação do primeiro alerta, em paralelo à construção das Observability Tables.

### 20.7 Etapa 7 — Indicadores dos cinco pilares

| Ordem | Atividade | Dependência | Resultado | Prioridade |
|---:|---|---|---|---|
| 41 | Formalizar execuções esperadas e horários dos ativos piloto | 3 e 5 | Base para detectar ausência e atraso | P0 |
| 42 | Implementar freshness técnica e, quando disponível, freshness de negócio | 24, 26 e 41 | Indicadores de atualidade | P0 |
| 43 | Coletar baseline e implementar indicadores de volume | 24 e 26 | Variações e cargas vazias identificadas | P0 |
| 44 | Criar snapshots de schema e comparação entre versões | 20 e 24 | Mudanças estruturais registradas | P1 |
| 45 | Selecionar regras mínimas de qualidade dos dez ativos | 2 a 4 | Escopo de DQ definido | P1 |
| 46 | Implementar o contrato canônico, adapters, geração de `quality_result_id` e `MERGE` dos resultados | 21 e 45 | Resultados de qualidade normalizados e idempotentes | P1 |
| 47 | Materializar lineage interno disponível no Unity Catalog | 10, 20 e 24 | Relações internas consultáveis | P1 |
| 48 | Calcular cobertura por tipo de evidência | 47 | Cobertura de lineage transparente | P1 |
| 49 | Implementar o Data Health Score experimental | 42 a 48 | Score por ativo, produto e domínio | P1 |

Freshness e volume devem ser concluídos antes das regras mais avançadas. O Health Score somente deve ser publicado quando seus componentes e dados ausentes forem tratados explicitamente.

### 20.8 Etapa 8 — Dashboards

| Ordem | Atividade | Dependência | Resultado | Prioridade |
|---:|---|---|---|---|
| 50 | Criar as visões curated para consumo | 30 e 42 a 48 | Camada de apresentação estável | P0 |
| 51 | Construir o dashboard operacional | 50 | Falhas, atrasos e anomalias visíveis | P0 |
| 52 | Implementar drill-down ADF → Activity → Job → Task | 30, 33 e 51 | Investigação correlacionada | P0 |
| 53 | Exibir falhas com notificação suprimida | 31 e 51 | Evidência preservada | P0 |
| 54 | Construir o dashboard executivo | 49 e 50 | Saúde, cobertura e tendência | P1 |
| 55 | Validar linguagem, filtros e leitura com os públicos-alvo | 51 e 54 | Dashboards aceitos | P1 |

### 20.9 Etapa 9 — Lineage federado

| Ordem | Atividade | Dependência | Resultado | Prioridade |
|---:|---|---|---|---|
| 56 | Ler e validar tecnicamente as tags e metadados de origem existentes nos ativos piloto | 4 e 20 | Entradas disponíveis para o sincronizador | P1 |
| 57 | Projetar a reconciliação de objetos externos SAP/BW/Datasphere | 56 | Chaves estáveis, `upsert`, auditoria e remoção segura definidos | P1 |
| 58 | Implementar uma PoC do sincronizador objeto externo → tabela Databricks | 47 e 57 | External lineage idempotente de origem demonstrado | P1 |
| 59 | Validar a conta de serviço e os workspaces Power BI acessíveis | 12 | Escopo real de Power BI conhecido | P1 |
| 60 | Testar as APIs disponíveis para metadados e lineage | 59 | Permissões e limitações documentadas | P1 |
| 61 | Avaliar e executar uma PoC do projeto open source `lineage-syncer` | 60 | Lineage Databricks → Power BI demonstrado | P1 |
| 62 | Testar idempotência, atualização, órfãos e remoção de relações obsoletas nos dois sincronizadores | 58 e 61 | Ciclo de vida validado | P1 |

As trilhas SAP e Power BI podem ocorrer em paralelo. Se dependerem de autorizações externas, não devem bloquear os dashboards do núcleo do MVP.

Após as PoCs, os sincronizadores aprovados deverão ser agendados com frequência de uma ou duas execuções por dia. Não há requisito para atualização de lineage a cada ciclo de 30–60 minutos.

### 20.10 Etapa 10 — MLOps opcional

| Ordem | Atividade | Dependência | Resultado | Prioridade |
|---:|---|---|---|---|
| 63 | Selecionar até três workloads representativos | Núcleo P0 estável | Amostra MLOps definida | P2 |
| 64 | Coletar inventário e status básicos de MLflow | 63 | Treinamentos visíveis | P2 |
| 65 | Coletar estado, volume, erros e latência de Model Serving | 63 | Serving básico visível | P2 |
| 66 | Avaliar inventário básico de Azure ML | 63 | Terceiro cenário documentado | P2 |

MLOps deve ser reduzido ou adiado se competir com atividades P0 ou P1.

### 20.11 Etapa 11 — Validação, custos e encerramento

| Ordem | Atividade | Dependência | Resultado | Prioridade |
|---:|---|---|---|---|
| 67 | Executar o conjunto de testes da seção 21 | Entregas correspondentes | Evidências de aceite | P0 |
| 68 | Medir ingestão, compute, armazenamento e alertas | 17, 26 e 39 | Custo real do MVP | P0 |
| 69 | Medir cobertura, latência, taxa de correlação e esforço de manutenção | 33, 40, 49 e 55 | Indicadores finais | P0 |
| 70 | Documentar operação, troubleshooting e recuperação | 67 | Runbook inicial | P0 |
| 71 | Registrar limitações e backlog da próxima onda | 58 a 69 | Lacunas priorizadas | P0 |
| 72 | Comparar evolução interna e ferramenta especializada | 68, 69 e 71 | Recomendação de continuidade | P1 |
| 73 | Apresentar resultados e obter aceite do MVP | 67 a 72 | Encerramento formal | P0 |

### 20.12 Distribuição sugerida nos 90 dias

| Período | Etapas predominantes | Marco esperado |
|---|---|---|
| Dias 1–15 | Etapas 1 e 2 | Escopo, inventário, acessos e critérios de aceite |
| Dias 16–30 | Etapas 3 e início da 4 | Logs ADF, Teams e estrutura das Observability Tables |
| Dias 31–45 | Etapas 4, 5 e início da 6 | Coleta integrada e correlação ADF–Databricks |
| Dias 46–60 | Etapas 6, 7 e início da 8 | Alertas como código, freshness, volume e dashboard operacional |
| Dias 61–75 | Etapas 7, 8 e 9 | DQ, schema, Health Score e PoCs de lineage |
| Dias 76–90 | Etapas 9, 10 e 11 | Lineage, MLOps opcional, custos, aceite e recomendação |

### 20.13 Caminho crítico

O caminho crítico do MVP é:

```text
selecionar ativos
  → obter acessos
  → coletar ADF e Databricks
  → construir as Observability Tables
  → correlacionar execuções
  → calcular freshness e volume
  → publicar o dashboard operacional
  → validar, medir custo e encerrar
```

Alertas como código devem ser concluídos no MVP, mas seu desenvolvimento pode ocorrer em paralelo às Observability Tables. Lineage federado, MLOps, Health Score completo e dashboard executivo não devem atrasar o caminho crítico caso dependências externas ou a capacidade de 1,5 FTE imponham restrições.

## 21. Testes e critérios de aceite

### 21.1 ADF

- Três categorias chegam ao Log Analytics.
- Falha controlada gera alerta e mensagem no Teams.
- Mensagem contém domínio, recurso, pipeline, horário e link.
- Logs ficam disponíveis pelo período definido.

### 21.2 Databricks

- A biblioteca corporativa resolve corretamente `dev_it` ou `prd_it` conforme o ambiente.
- Apenas os workspaces retornados por `corp_curated.vw_vc_workspaces` são materializados.
- Um workspace incluído na view entra no próximo ciclo sem alteração de código.
- Um workspace removido deixa de ser coletado sem apagar seu histórico.
- A indisponibilidade da view interrompe a coleta sem incluir workspaces fora do escopo.
- Jobs, runs e tasks são idempotentes.
- Execução independente é distinguida de execução ADF.
- As tabelas são criadas em `dev_governance` ou `prd_governance` conforme o ambiente e seguem o padrão corporativo de nomes.

### 21.3 Correlação

- UUID final é extraído sem decompor o restante do nome.
- Join com ActivityRun funciona para diferentes ADFs.
- Cobertura mínima de 95% nos casos elegíveis.
- Falha Databricks correlacionada aparece no dashboard.
- A mesma falha não gera notificação Databricks adicional.

### 21.4 YAML/CI/CD

- YAML inválido bloqueia o pipeline.
- Alias desconhecido bloqueia o pipeline.
- `plan` mostra alterações.
- `apply` é idempotente.
- Recurso manual não é excluído.
- Drift é reportado.
- Todos os YAML em `alerts/**/*.yaml` são descobertos e validados.
- O mesmo commit aprovado é implantado no Azure Monitor e sincronizado na Databricks Git Folder.
- O processo Databricks consegue ler da Git Folder as regras de execução e de Data Quality.

### 21.5 Data Quality

- Expectations, DQM e regras SQL customizadas produzem o mesmo schema canônico.
- `evaluation_status` diferencia erro técnico de `quality_outcome` reprovado.
- O mesmo evento reingerido produz o mesmo `quality_result_id`.
- Uma nova avaliação da mesma regra produz um novo `quality_result_id`.
- O `MERGE` não cria duplicidades.
- Timestamps de avaliação são preservados em UTC e não substituídos pelo horário de ingestão.
- Campos obrigatórios ausentes são rejeitados e registrados como falha técnica do adapter.
- Registros de negócio inválidos não são copiados para as Observability Tables.

### 21.6 Freshness e volume

- `DESCRIBE DETAIL` retorna `lastModified`, `sizeInBytes`, `numFiles` e `createdAt` para todos os ativos monitorados.
- O teste de referência com compute já ativo processa 181 tabelas em aproximadamente 41,33 segundos, admitindo nova calibração no ambiente definitivo.
- O benchmark de produção inclui a materialização ou escrita do resultado, e o tempo de inicialização do compute é medido separadamente.
- A reexecução do mesmo ciclo não duplica estados na cleansed.
- A variação de `sizeInBytes` e `numFiles` é preservada para análise de evolução física.
- O dashboard identifica claramente volume físico e não apresenta essas métricas como contagem de registros.
- Um ativo com `OPTIMIZE` ou `VACUUM` separado pode ser configurado para validação complementar por `DESCRIBE HISTORY`.
- A coleta de freshness compartilha o workflow operacional e não cria uma segunda inicialização de compute por ciclo.

### 21.7 Lineage

- Uma origem SAP é ligada a um ativo raw como evidência declarada.
- Um fluxo raw → cleansed → curated apresenta lineage nativo.
- Um modelo ou relatório Power BI acessível é ligado ao ativo curated, quando as APIs permitirem.
- O tipo de evidência é visível.
- Reprocessar o mesmo conjunto não duplica objetos nem relações.
- Uma relação que deixou de ser declarada é removida após a regra de carência definida.
- Um objeto externo compartilhado não é excluído enquanto possuir outra relação ativa.
- O agendamento executa a atualização de lineage uma ou duas vezes ao dia, independentemente do ciclo operacional de 30–60 minutos.
- Objetos que não foram criados pelo sincronizador não são alterados nem excluídos.
- Uma falha temporária de API ou permissão não provoca exclusão imediata de objetos Power BI.
- Inclusões, atualizações, desativações e exclusões ficam registradas para auditoria.

### 21.8 Dashboards

- Atualização dentro de 60 minutos.
- Drill-down ADF → Activity → Databricks Job → Task.
- Ativos críticos e violações não são ocultados pela média.
- Eventos suprimidos continuam visíveis.

## 22. Custos a mensurar

### 22.1 Log Analytics

- GB ingeridos por categoria;
- retenção;
- consultas;
- alertas de log;
- número de séries por regra.

A volumetria existente estima aproximadamente 4,5 GB/mês somente para as três categorias ADF antes de margem. O piloto deverá substituir as premissas de bytes/evento por valores reais.

### 22.2 Azure Monitor e notificações

- regras métricas;
- Scheduled Query Rules;
- frequência de avaliação;
- séries por regra;
- Logic App;
- conectores de Teams.

A planilha mostra que o custo potencial dos alertas pode superar o custo de ingestão quando há muitas regras e séries. Templates globais e agrupamento devem ser priorizados.

### 22.3 Databricks

- jobs de coleta;
- inicialização ou provisionamento do compute, medida separadamente do processamento;
- execução de `DESCRIBE DETAIL` no mesmo workflow operacional, com referência inicial de 41,33 segundos para 181 tabelas e premissa orçamentária de um minuto por ciclo;
- SQL Warehouse;
- materialização das Observability Tables;
- DQM/serverless quando utilizado;
- dashboard e consultas;
- retenção e otimização Delta.

### 22.4 Pessoas

- esforço de implementação;
- sustentação mensal;
- onboarding de ativo;
- manutenção das regras;
- atendimento de incidentes;
- evolução do lineage.

## 23. Alternativas técnicas registradas

| Tema | Decisão do MVP | Alternativa futura |
|---|---|---|
| Ingestão ADF | Log Analytics | Storage para arquivo ou Event Hub para streaming |
| Databricks jobs | System Tables | Eventos customizados somente quando necessário |
| Histórico | Delta/Unity Catalog | Plataforma especializada |
| Alertas ADF | Azure Monitor | Plataforma corporativa de observabilidade |
| Configuração | YAML + CI/CD + SDK | Terraform/Bicep ou produto especializado |
| Teams | Logic App | Integração corporativa padronizada |
| Power BI lineage | APIs/workspaces acessíveis + PoC | Purview ou ferramenta especializada |
| SAP lineage | Tags/metadata/external asset | Conector corporativo certificado |
| Streaming | Fora do MVP | Event Hub |
| ITSM | Fora do MVP | ServiceNow |

## 24. Critérios para evolução ou aquisição

Após o MVP, avaliar ferramenta especializada quando houver evidência de uma ou mais condições:

- cobertura crítica abaixo de 80% mesmo após automação;
- esforço de sustentação incompatível com a equipe;
- onboarding de ativos excessivamente manual;
- análise de causa raiz (RCA) depende continuamente de cruzamento manual entre plataformas;
- lineage SAP–Databricks–Power BI insuficiente para auditoria;
- necessidade corporativa multicloud;
- MTTD ou MTTR permanecem acima das metas;
- anomalias avançadas e análise de causa raiz (RCA) automática tornam-se requisitos;
- custo interno se aproxima do custo de uma solução gerenciada;
- requisitos de segurança, segregação ou compliance não são atendidos.

## 25. Backlog posterior ao MVP

- ServiceNow.
- Event Hub, se a latência mudar.
- Data Contracts ODCS e validação em CI/CD.
- External lineage em escala.
- Integração corporativa com SAP/Purview.
- Anomalias multivariadas.
- Análise de causa raiz (RCA) assistida ou automatizada.
- Drift e performance avançada de modelos.
- Runbooks automatizados.
- Expansão para VCNA, Itacamba e VCEAA.
- Avaliação de ferramenta multicloud.

## 26. Proposta para o terceiro nível de documentação

Este desenho técnico define a arquitetura de referência, mas não deve concentrar todos os contratos, schemas, algoritmos e casos de teste necessários à construção. Antes do desenvolvimento, recomenda-se criar um terceiro nível: **Especificações Detalhadas de Implementação**.

### 26.1 Estrutura proposta

```text
docs/
├── conceptual/
│   └── solucao-conceitual.md
├── architecture/
│   └── desenho-tecnico-referencia.md
├── decisions/
│   ├── adr-001-identidade-quality-result.md
│   ├── adr-002-escopo-workspaces-vc.md
│   └── adr-003-modelo-time-suporte.md
└── implementation/
    ├── 00-visao-geral.md
    ├── 01-modelo-observability-tables.md
    ├── 02-ingestao-adf-log-analytics.md
    ├── 03-ingestao-system-tables.md
    ├── 04-correlacao-adf-databricks.md
    ├── 05-quality-results-e-adapters.md
    ├── 06-alertas-yaml-e-cicd.md
    ├── 07-indicadores-e-health-score.md
    ├── 08-external-lineage-sap.md
    ├── 09-lineage-power-bi.md
    ├── 10-dashboards.md
    ├── 11-seguranca-e-permissoes.md
    └── 12-plano-de-testes.md
```

Os caminhos e nomes de arquivos são uma proposta organizacional e poderão ser adaptados ao repositório definitivo.

### 26.2 Conteúdo mínimo de cada especificação

Cada componente deverá documentar:

1. objetivo e limites;
2. dependências e permissões;
3. entradas e saídas;
4. tabelas, campos, tipos e chaves;
5. regras de transformação;
6. tratamento de timestamps e timezone;
7. idempotência, watermark e reprocessamento;
8. erros, retries e comportamento de recuperação;
9. segurança e dados que não podem ser persistidos;
10. observabilidade do próprio componente;
11. implantação e rollback;
12. testes unitários, integrados e critérios de aceite;
13. exemplos de payload, query ou chamada;
14. decisões abertas.

### 26.3 Uso do método BMAD

Caso o método BMAD seja adotado, não é necessário recriar do zero os documentos existentes. Eles poderão ser usados como insumos:

| Artefato existente | Uso no projeto BMAD |
|---|---|
| Solução conceitual | Contexto, problema, objetivos e escopo de negócio |
| Desenho técnico de referência | Arquitetura, decisões e requisitos não funcionais |
| Plano sequencial de atividades | Base para épicos, dependências e priorização |
| Especificações detalhadas | Base para histórias de implementação e testes |
| ADRs | Registro das decisões técnicas e alternativas consideradas |

Fluxo recomendado:

```text
refinar e aprovar o desenho técnico
        ↓
criar o projeto/repositório de implementação
        ↓
criar as especificações detalhadas por componente
        ↓
transformar atividades em épicos e histórias
        ↓
implementar, testar e atualizar os documentos
```

Com 1,5 FTE, recomenda-se uma adoção leve do BMAD: reutilizar os artefatos existentes, produzir apenas as especificações necessárias à próxima etapa e evitar documentação duplicada sem uso direto na implementação.

### 26.4 Primeira especificação a produzir

O primeiro documento do terceiro nível deverá ser `01-modelo-observability-tables.md`, pois os coletores, adapters, indicadores e dashboards dependem das definições de tabela, campo e chave.

Em seguida:

1. `02-ingestao-adf-log-analytics.md`;
2. `03-ingestao-system-tables.md`;
3. `04-correlacao-adf-databricks.md`;
4. `05-quality-results-e-adapters.md`;
5. `06-alertas-yaml-e-cicd.md`.

Lineage federado, dashboards e MLOps deverão ser especificados depois que o núcleo de ingestão e persistência estiver estável.

---

## Conclusão técnica

O desenho utiliza Log Analytics onde ele oferece maior valor — operação recente e alertas do ADF — e System Tables onde o Databricks já fornece telemetria estruturada. As Observability Tables integram os sinais sem transformar o Log Analytics em repositório histórico. A correlação automática pelo Activity Run ID reduz instrumentação adicional, enquanto a separação entre evento e notificação preserva diagnóstico sem duplicar mensagens do Databricks.

O uso de YAML e CI/CD cria governança de alertas com um componente de software pequeno e controlável. SAP e Power BI entram de forma federada, respeitando permissões e processos existentes. Esse núcleo é compatível com o limite de 1,5 FTE desde que o MVP permaneça restrito aos dez ativos e priorize operação, correlação e visibilidade antes das funcionalidades avançadas.
