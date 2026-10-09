# Tabelas Delta da Observabilidade no Databricks

Este documento descreve todas as tabelas que a solução de Observabilidade cria ou alimenta no Databricks (Unity Catalog): para que servem, quem grava, quem lê, como são criadas e o que significa cada coluna. As regras de cada processo estão nas especificações, em [Especs/](Especs/00-Indice.md).

## Convenções

**Catálogo por ambiente.** As tabelas de governança ficam no catálogo `dev_governance` em desenvolvimento e `prd_governance` em produção. Neste documento ele aparece como `<gov>`. A tabela de log de pipelines fica no catálogo de TI do ambiente (`<env>_it`).

**Nomes.** Seguem o padrão corporativo `tipagem_origem_dominio_nome`, com tipagem `tbl_` e domínio `it` (serviço de TI). A origem `dbr` marca o que nasce no Databricks; `db`, o que vem do banco relacional (o hub em Azure SQL). Não existe tipagem `stg_` no padrão corporativo: a tabela de staging é uma `tbl_` como qualquer outra, e o papel dela está no nome.

**Fuso.** Todas as colunas `TIMESTAMP` gravadas pelos jobs desta solução guardam o instante em UTC. A exceção é a tabela de log de pipelines, cujos `started_at` e `ended_at` são o horário do host (ver a seção 9).

**PRIMARY KEY.** Onde aparece, é a constraint informativa do Unity Catalog (`NOT ENFORCED`): documenta o grão da tabela, mas o Delta não rejeita duplicatas por causa dela.

## Visão geral

| # | Tabela | Schema | Quem grava | Quem lê | Como é criada |
|---|---|---|---|---|---|
| 1 | `tbl_dbr_it_asset_observability` | `<gov>.corp_cleansed` | asset_observability | asset_observability (observação anterior), análise | DDL do job, na primeira execução |
| 2 | `tbl_dbr_it_asset_commit_metrics` | `<gov>.corp_cleansed` | asset_observability | asset_observability (watermark), análise | DDL do job, na primeira execução |
| 3 | `tbl_dbr_it_observability_events` | `<gov>.corp_cleansed` | asset_observability, data_quality, schema_observability | hub-publisher | DDL do asset_observability, na primeira execução |
| 4 | `tbl_dbr_it_data_quality_results` | `<gov>.corp_cleansed` | data_quality | Análise, Power BI | DDL do job, no início de cada execução (`IF NOT EXISTS`) |
| 5 | `tbl_dbr_it_schema_snapshot` | `<gov>.corp_cleansed` | schema_observability | schema_observability (execução anterior) | Primeiro `saveAsTable`, com tipos inferidos |
| 6 | `tbl_dbr_it_schema_changes` | `<gov>.corp_cleansed` | schema_observability | Análise, auditoria | Primeiro `saveAsTable`, com tipos inferidos |
| 7 | `tbl_db_it_hub_executions` | `<gov>.corp_raw` e `<gov>.corp_cleansed` | hub-replication | Power BI executivo, histórico | Fora do job (o `COPY INTO` não cria tabela) |
| 8 | `tbl_db_it_hub_problems` | `<gov>.corp_raw` e `<gov>.corp_cleansed` | hub-replication | Power BI executivo, histórico | Fora do job (o `COPY INTO` não cria tabela) |
| 9 | `tbl_dbr_it_log_execucao_pipeline` | `<env>_it.corp_raw` | `vc_observability.pipeline_logging`, chamado pelos notebooks de carga | Análise de cargas | DDL do módulo (`build_create_statement`), tabela `EXTERNAL` |

Além das tabelas, o hub-replication usa o Volume `<gov>.corp_raw.hub_replication_staging` como pouso dos arquivos CSV antes do `COPY INTO`.

As tabelas 1 a 3 recebem, na criação, as tags de governança de `GOVERNANCE_TABLE_TAGS` (`asset_observability/constants.py`): `vc_source_system = databricks`, `vc_source_object = several`, `vc_company = vcbr`, `vc_contains_pii = false`, `vc_criticality = low`, `vc_data_classification = internal`, `vc_data_domain = it`, `vc_data_quality_tier = experimental`, `vc_data_retention = 2y`, `vc_lifecycle_status = active`, `vc_refresh_frequency = 1d`, mais `vc_medallion_layer = cleansed`, derivada do schema. O asset_observability não observa essas três tabelas, mesmo com a tag de frequência: elas são excluídas da varredura para o job não abrir incidente sobre a própria saída.

---

## 1. `tbl_dbr_it_asset_observability`

**Para que serve.** Histórico das observações de freshness e volume de cada tabela Delta varrida pelo asset_observability. A última observação de cada ativo é a referência para calcular a variação de volume na execução seguinte. Especificação: [20](Especs/20-Databricks-asset_observability.md).

| Item | Valor |
|---|---|
| Nome completo | `<gov>.corp_cleansed.tbl_dbr_it_asset_observability` |
| Grão | Uma linha por ativo por execução do job |
| Chave | PK `(asset, observed_at)` |
| Escrita | Append, ao fim de cada schema varrido |
| Retenção | Sem limpeza automática; tag `vc_data_retention = 2y` |

| Coluna | Tipo | Nulo | Descrição |
|---|---|---|---|
| `asset` | `STRING` | Não | Nome completo do ativo observado (`catálogo.schema.tabela`) |
| `updated_at` | `TIMESTAMP` | Sim | `lastModified` do `DESCRIBE DETAIL`. `NULL` quando o job tentou ler o ativo e não conseguiu (linha de tentativa) |
| `size_bytes` | `BIGINT` | Sim | `sizeInBytes` do `DESCRIBE DETAIL`. Zero na linha de tentativa |
| `file_count` | `BIGINT` | Sim | `numFiles` do `DESCRIBE DETAIL`. Zero na linha de tentativa |
| `row_count` | `BIGINT` | Sim | `COUNT(*)` do ativo. `NULL` quando a contagem falhou, que não é o mesmo que zero linhas |
| `observed_at` | `TIMESTAMP` | Não | Instante em que a execução coletou a observação |

**Linha de tentativa.** Quando o `DESCRIBE DETAIL` falha (tabela externa cujo caminho não existe mais, falta de permissão), o job grava a linha com `updated_at` nulo e métricas zeradas, para registrar que tentou. Essa linha não é avaliada nem usada como referência de volume. Para considerar só leituras reais, filtre `updated_at IS NOT NULL`. Uma tabela vazia de verdade tem `row_count = 0` **e** `updated_at` preenchido.

```sql
-- Última leitura real de cada ativo
SELECT * FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY asset ORDER BY observed_at DESC) AS rn
    FROM <gov>.corp_cleansed.tbl_dbr_it_asset_observability
   WHERE updated_at IS NOT NULL
) WHERE rn = 1;
```

## 2. `tbl_dbr_it_asset_commit_metrics`

**Para que serve.** Log das métricas de cada commit Delta dos ativos varridos. Guarda o histórico além dos 30 dias que o `DESCRIBE HISTORY` mantém e é a base para regras futuras de volume por carga (por exemplo, um `MERGE` que não inseriu nada). Nenhuma regra de alerta lê esta tabela. Também é a fonte do watermark: a maior `delta_version` de cada ativo diz até onde o job já leu. Especificação: [20](Especs/20-Databricks-asset_observability.md), seção 5.4.

| Item | Valor |
|---|---|
| Nome completo | `<gov>.corp_cleansed.tbl_dbr_it_asset_commit_metrics` |
| Grão | Uma linha por commit Delta de cada ativo, ou uma linha agregada quando a janela passa de 500 commits (`COMMIT_BATCH_CAP`) |
| Chave | PK `(asset, delta_version)` |
| Escrita | Append, ao fim de cada schema varrido |
| Retenção | 13 meses (`COMMIT_METRICS_RETENTION_MONTHS`), aplicada pelo próprio job ao fim de cada execução. O commit mais recente de cada ativo nunca é apagado, porque guarda o watermark |

| Coluna | Tipo | Nulo | Descrição |
|---|---|---|---|
| `asset` | `STRING` | Não | Nome completo do ativo dono do commit |
| `delta_version` | `BIGINT` | Não | Versão Delta do commit; na linha agregada, a última versão da janela |
| `commit_timestamp` | `TIMESTAMP` | Sim | Timestamp do commit no transaction log Delta |
| `operation` | `STRING` | Sim | Operação Delta (`WRITE`, `MERGE`, `OPTIMIZE`, ...), ou `AGGREGATED` na linha agregada |
| `commits_collapsed` | `BIGINT` | Sim | Quantos commits a linha resume; 1 fora do caso agregado |
| `has_gap` | `BOOLEAN` | Sim | `TRUE` na primeira linha depois de um buraco: o histórico Delta já tinha perdido versões que o job nunca leu |
| `observed_at` | `TIMESTAMP` | Não | Instante em que a execução coletou o commit |
| `num_output_rows` | `BIGINT` | Sim | `numOutputRows` do `operationMetrics` |
| `num_target_rows_inserted` | `BIGINT` | Sim | `numTargetRowsInserted` (`MERGE`) |
| `num_target_rows_updated` | `BIGINT` | Sim | `numTargetRowsUpdated` (`MERGE`) |
| `num_target_rows_deleted` | `BIGINT` | Sim | `numTargetRowsDeleted` (`MERGE`) |
| `num_target_rows_copied` | `BIGINT` | Sim | `numTargetRowsCopied`: linhas inalteradas reescritas num `MERGE`. Sem ela, `num_output_rows` de um `MERGE` engana |
| `num_source_rows` | `BIGINT` | Sim | `numSourceRows` |
| `num_deleted_rows` | `BIGINT` | Sim | `numDeletedRows` (`DELETE`) |
| `num_updated_rows` | `BIGINT` | Sim | `numUpdatedRows` (`UPDATE`) |
| `num_added_files` | `BIGINT` | Sim | `numAddedFiles` |
| `num_removed_files` | `BIGINT` | Sim | `numRemovedFiles` |
| `num_added_bytes` | `BIGINT` | Sim | `numAddedBytes` |
| `num_removed_bytes` | `BIGINT` | Sim | `numRemovedBytes` |

As métricas são `NULL` quando a operação não as publica, nunca zero: "a operação não informa" e "foram zero linhas" são respostas diferentes. Na linha agregada, cada métrica é a soma da janela.

## 3. `tbl_dbr_it_observability_events` (staging)

**Para que serve.** Fila de eventos que podem abrir ou fechar incidente no hub. Os jobs Databricks só escrevem aqui e nunca falam com o banco do hub. O hub-publisher (Container Apps Job) lê as linhas pendentes, chama `resolve_event()` no hub e marca `sent_at`. Especificação: [12](Especs/12-Job-hub-publisher.md), seção 4.

| Item | Valor |
|---|---|
| Nome completo | `<gov>.corp_cleansed.tbl_dbr_it_observability_events` |
| Grão | Um evento por avaliação: os dois resultados (`PASS` e `FAIL`) entram, porque o `FAIL` abre o incidente e o `PASS` fecha |
| Chave | PK `(source_event_id)` |
| Escrita | Append pelos produtores; `UPDATE ... SET sent_at` pelo hub-publisher |
| Pendentes | `sent_at IS NULL` |
| Retenção | Sem limpeza automática |

| Coluna | Tipo | Nulo | Descrição |
|---|---|---|---|
| `source` | `STRING` | Não | Pilar da avaliação: `FRESHNESS`, `VOLUME`, `DATA_QUALITY` ou `SCHEMA` |
| `result` | `STRING` | Não | `PASS` ou `FAIL` |
| `natural_key` | `STRING` | Não | O que foi avaliado (formato por produtor, abaixo) |
| `domain` | `STRING` | Sim | Domínio de negócio do ativo, usado nos relatórios |
| `severity` | `STRING` | Sim | `HIGH`, `MEDIUM` ou `LOW` |
| `error_code` | `STRING` | Sim | Código estável em inglês; `NULL` quando `result = PASS` |
| `description` | `STRING` | Sim | Mensagem em pt-BR para o operador, que chega ao Teams; `NULL` quando `result = PASS` |
| `source_event_id` | `STRING` | Não | Id determinístico do produtor; o hub-publisher o usa para reenviar sem duplicar |
| `observed_at` | `TIMESTAMP` | Não | Instante da execução que gerou o evento |
| `sent_at` | `TIMESTAMP` | Sim | Instante em que o hub-publisher confirmou o envio; `NULL` enquanto pendente |

| Produtor | `source` | `natural_key` | `error_code` | `source_event_id` |
|---|---|---|---|---|
| asset_observability | `FRESHNESS`, `VOLUME` | Nome completo do ativo | `ASSET_STALE`, `VOLUME_OUT_OF_RANGE`, `EMPTY_LOAD` | SHA-256 (hex) de fonte, ativo e instante da execução |
| data_quality | `DATA_QUALITY` | `<rule_id>@<ativo>` | `RULE_FAILED`, `RULE_NOT_EVALUATED` | `result_id` da tabela 4 (SHA-256 de ativo, regra, versão e instante) |
| schema_observability | `SCHEMA` | Nome completo da tabela | `SCHEMA_INCOMPATIBLE_CHANGE`, `SCHEMA_CRITICAL_CHANGE` | `SCHEMA:<tabela>:<AAAAMMDD>` |

O data_quality só publica as regras bloqueantes (`blocking: true`), e o schema_observability só publica `FAIL` de mudança incompatível ou crítica em ativo com consumidor declarado.

## 4. `tbl_dbr_it_data_quality_results`

**Para que serve.** Histórico completo das regras de Data Quality: todas as regras de todas as execuções, bloqueantes ou não, aprovadas ou reprovadas. Só as bloqueantes viram evento na staging. Separa "a regra não conseguiu rodar" (`evaluation_status = ERROR`) de "o dado está ruim" (`quality_outcome = FAIL`). Especificação: [21](Especs/21-Databricks-data_quality.md).

| Item | Valor |
|---|---|
| Nome completo | `<gov>.corp_cleansed.tbl_dbr_it_data_quality_results` |
| Grão | Uma linha por regra por execução |
| Chave | PK `(result_id)` |
| Escrita | Append, por arquivo de regras |
| Retenção | Sem limpeza automática |

| Coluna | Tipo | Nulo | Descrição |
|---|---|---|---|
| `result_id` | `STRING` | Não | SHA-256 de ativo, regra, versão e instante da execução. Também é o `source_event_id` do evento |
| `rule_id` | `STRING` | Não | Id estável da regra no YAML; nunca é reaproveitado |
| `rule_version` | `STRING` | Não | Versão da regra; muda quando o critério muda |
| `asset` | `STRING` | Não | Nome completo do ativo avaliado |
| `rule_type` | `STRING` | Não | `not_null`, `unique`, `domain`, `range`, `reference` ou `expression` |
| `severity` | `STRING` | Sim | Severidade declarada na regra |
| `blocking` | `BOOLEAN` | Sim | `TRUE` quando a regra pode abrir incidente |
| `evaluation_status` | `STRING` | Não | `SUCCESS` ou `ERROR`: a regra conseguiu rodar? |
| `quality_outcome` | `STRING` | Não | `PASS`, `WARN`, `FAIL` ou `UNKNOWN`: o dado está bom? `UNKNOWN` acompanha `ERROR` |
| `evaluated_row_count` | `BIGINT` | Sim | Linhas avaliadas pela regra |
| `invalid_row_count` | `BIGINT` | Sim | Linhas inválidas encontradas |
| `invalid_pct` | `DOUBLE` | Sim | Percentual de inválidas sobre as avaliadas |
| `error` | `STRING` | Sim | Mensagem técnica (até 500 caracteres) quando `evaluation_status = ERROR` |
| `executed_at` | `TIMESTAMP` | Não | Instante da execução |

`FAIL` só acontece em regra bloqueante; uma regra não bloqueante acima do limite fica `WARN`.

```sql
-- Regras que não conseguiram rodar na última semana
SELECT asset, rule_id, rule_version, error, executed_at
  FROM <gov>.corp_cleansed.tbl_dbr_it_data_quality_results
 WHERE evaluation_status = 'ERROR'
   AND executed_at >= current_timestamp() - INTERVAL 7 DAYS;
```

## 5. `tbl_dbr_it_schema_snapshot`

**Para que serve.** Estado das colunas de cada tabela monitorada em cada execução do schema_observability. A comparação entre a execução atual e a anterior gera as mudanças da tabela 6. Especificação: [22](Especs/22-Databricks-schema_observability.md).

| Item | Valor |
|---|---|
| Nome completo | `<gov>.corp_cleansed.tbl_dbr_it_schema_snapshot` |
| Grão | Uma linha por coluna por tabela por execução |
| Chave | Sem PK declarada; o grão é `(table, column, observed_at)` |
| Escrita | Append; nunca é sobrescrita |
| Retenção | Sem limpeza automática |

| Coluna | Tipo | Nulo | Descrição |
|---|---|---|---|
| `table` | `STRING` | Não | Nome completo da tabela |
| `column` | `STRING` | Não | Nome da coluna |
| `ordinal_position` | `BIGINT` | Não | Posição da coluna na tabela |
| `data_type` | `STRING` | Não | Tipo completo da coluna (`full_data_type` do `information_schema`) |
| `is_nullable` | `BOOLEAN` | Não | Se a coluna aceita nulo |
| `observed_at` | `TIMESTAMP` | Não | Instante da execução |

Os tipos são os que o Spark inferiu na primeira gravação. `table` e `column` são palavras reservadas em SQL: nas consultas, escreva entre crases (`` `table` ``, `` `column` ``).

## 6. `tbl_dbr_it_schema_changes`

**Para que serve.** Log de auditoria das mudanças de estrutura detectadas, alertáveis ou não. O alerta é só o subconjunto incompatível ou crítico em ativo com consumidor declarado. Especificação: [22](Especs/22-Databricks-schema_observability.md).

| Item | Valor |
|---|---|
| Nome completo | `<gov>.corp_cleansed.tbl_dbr_it_schema_changes` |
| Grão | Uma linha por mudança detectada |
| Chave | Sem PK declarada |
| Escrita | Append |
| Retenção | Log de auditoria; nunca é limpo |

| Coluna | Tipo | Nulo | Descrição |
|---|---|---|---|
| `table` | `STRING` | Não | Nome completo da tabela |
| `domain` | `STRING` | Sim | Domínio da tabela (tag ou YAML de ativos) |
| `severity` | `STRING` | Sim | Severidade derivada da criticidade |
| `column` | `STRING` | Sim | Coluna afetada; `NULL` quando a mudança é da tabela inteira (`TABLE_DROPPED`) |
| `change_type` | `STRING` | Não | `COLUMN_ADDED`, `COLUMN_DROPPED`, `TYPE_CHANGED`, `NULLABILITY_TIGHTENED`, `NULLABILITY_RELAXED` ou `TABLE_DROPPED` |
| `classification` | `STRING` | Não | `COMPATIVEL`, `CONTROLADA`, `INCOMPATIVEL` ou `CRITICA` |
| `previous_value` | `STRING` | Sim | Valor anterior (tipo ou nulidade) |
| `current_value` | `STRING` | Sim | Valor atual |
| `detected_at` | `TIMESTAMP` | Não | Instante da execução que detectou |

Os tipos são os que o Spark inferiu na primeira gravação. Valem as mesmas crases da tabela 5 para `table` e `column`.

## 7. `tbl_db_it_hub_executions`

**Para que serve.** Réplica do log de execuções do hub (`tbl_executions`, no Azure SQL), para manter histórico de dois anos e alimentar o Power BI executivo. A réplica é também a condição para a purga do hub: nada é apagado lá antes de chegar aqui. Especificação: [13](Especs/13-Job-hub-replication.md).

| Item | Valor |
|---|---|
| Nomes completos | `<gov>.corp_raw.tbl_db_it_hub_executions` e `<gov>.corp_cleansed.tbl_db_it_hub_executions`, com cópias idênticas |
| Grão | Uma linha por execução observada (Databricks ou ADF) |
| Escrita | `COPY INTO` a partir de CSV no Volume `hub_replication_staging`, de hora em hora, pelo hub-replication (Container Apps Job) |
| Idempotência | O `COPY INTO` não recarrega um arquivo já processado, e o nome do arquivo é derivado do watermark |

| Coluna | Tipo no hub (Azure SQL) | Descrição |
|---|---|---|
| `source` | `VARCHAR(30)` | Origem do sinal (`DATABRICKS`, `ADF`, ...) |
| `scope` | `VARCHAR(50)` | `workspace_id` (Databricks) ou nome do Data Factory (ADF) |
| `natural_key` | `NVARCHAR(400)` | Identidade estável do job ou pipeline |
| `run_id` | `VARCHAR(80)` | Id da execução na origem |
| `run_name` | `NVARCHAR(500)` | Nome da execução na origem |
| `status` | `VARCHAR(30)` | Estado da execução |
| `result_state` | `VARCHAR(30)` | Resultado final, quando houver |
| `capture_source` | `VARCHAR(20)` | `API` ou `LOG_ANALYTICS`: por onde o coletor viu a execução |
| `observed_at` | `DATETIMEOFFSET(3)` | Instante da execução observada |

Os tipos no Delta vêm da definição da tabela de destino; o CSV de pouso traz datas em ISO 8601.

## 8. `tbl_db_it_hub_problems`

**Para que serve.** Réplica dos incidentes **resolvidos** do hub (`tbl_problems` com `incident_status = 'RESOLVED'`). Um incidente resolvido não muda mais (um novo `FAIL` abre outro incidente, com outro `problem_id`), por isso a réplica é só de inserção. Especificação: [13](Especs/13-Job-hub-replication.md).

| Item | Valor |
|---|---|
| Nomes completos | `<gov>.corp_raw.tbl_db_it_hub_problems` e `<gov>.corp_cleansed.tbl_db_it_hub_problems`, com cópias idênticas |
| Grão | Um incidente resolvido |
| Escrita | Mesmo mecanismo da tabela 7, com watermark sobre `closed_at` |

| Coluna | Tipo no hub (Azure SQL) | Descrição |
|---|---|---|
| `problem_id` | `UNIQUEIDENTIFIER` | Id do incidente |
| `source` | `VARCHAR(30)` | Quem detectou primeiro |
| `correlation_key` | `NVARCHAR(400)` | Identifica o incidente (pode reunir sinais de mais de uma origem) |
| `natural_key` | `NVARCHAR(400)` | Identifica a origem do sinal |
| `key_rule` | `VARCHAR(24)` | Regra que gerou a chave (`JOB_ID`, `RUN_NAME`, `ADF_ACTIVITY`, ...) |
| `workspace_id` | `VARCHAR(50)` | Workspace Databricks, quando se aplica |
| `domain` | `VARCHAR(50)` | Domínio de negócio do incidente |
| `incident_status` | `VARCHAR(10)` | Sempre `RESOLVED` nesta réplica |
| `severity` | `VARCHAR(20)` | Severidade do incidente |
| `error_code` | `VARCHAR(50)` | Código do erro |
| `error_description` | `NVARCHAR(1000)` | Descrição do erro |
| `opening_run_id` | `VARCHAR(80)` | Execução que abriu o incidente |
| `opened_at` | `DATETIMEOFFSET(3)` | Abertura |
| `closing_run_id` | `VARCHAR(80)` | Execução que fechou o incidente |
| `closed_at` | `DATETIMEOFFSET(3)` | Fechamento |
| `occurrence_count` | `INT` | Quantas vezes o problema foi visto enquanto aberto |

## 9. `tbl_dbr_it_log_execucao_pipeline`

**Para que serve.** Log de cada operação de carga dos notebooks das camadas raw, cleansed e curated: duração, volume, tentativas e falhas, sem depender do log do driver. É escrito pelo módulo `vc_observability.pipeline_logging` (`EventLogger`), que é a única fonte do DDL. Arquitetura: [pipeline-execution-logging](architecture/pipeline-execution-logging.md).

| Item | Valor |
|---|---|
| Nome completo | `<env>_it.corp_raw.tbl_dbr_it_log_execucao_pipeline` |
| Tipo | `EXTERNAL`, em `abfss://raw@<storage>.dfs.core.windows.net/IT/PipelineLog/tbl_dbr_it_log_execucao_pipeline` (`sadatalakedevvcbr` em DEV, `sadatalakevcbr` em PRD) |
| Grão | Um evento por operação por tabela por camada por tentativa |
| Chave | `execution_id` + `event_seq` (documentada no comentário da tabela) |
| Layout | `CLUSTER BY (dt_execution, layer, target_table)`; Change Data Feed e deletion vectors ligados |
| Escrita | Append-only: correção entra como evento novo, nunca como `UPDATE` |
| Evolução | Só aditiva (`ALTER TABLE ADD COLUMNS`), coluna nova sempre no fim |
| Retenção | `VACUUM` padrão de 168 horas |

| Coluna | Tipo | Descrição |
|---|---|---|
| `execution_batch_id` | `STRING` | UUID do disparo; agrupa todas as tabelas de um mesmo run |
| `execution_id` | `STRING` | `<target_table>_<yyyyMMddHHmmss do início real>` |
| `event_seq` | `INT` | Ordem do evento na execução (1 = `STARTED`, N = terminal) |
| `environment` | `STRING` | `DEV` ou `PRD` |
| `layer` | `STRING` | `raw`, `cleansed` ou `curated` |
| `domain` | `STRING` | Glossário controlado: `pro`, `fin`, `log`, `mdm`, `com`, `ind`, `tax`, `ctrl`, `it`, `sus`, `hr`, `qlty`, `eng` |
| `target_table` | `STRING` | Nome completo da tabela carregada |
| `source_system` | `STRING` | Sistema de origem, mesmo vocabulário de `vc_source_system` (`ds`, `bw`, `sql`, `postgres`, `api`, `flat_file`, `databricks`) |
| `source_object` | `STRING` | Objeto de origem: CDS view na raw, nome completo da tabela nas demais camadas |
| `load_strategy` | `STRING` | `FULL`, `DELTA`, `APPEND` ou `SNAPSHOT` |
| `started_at` | `TIMESTAMP` | Início real da operação, horário do host |
| `ended_at` | `TIMESTAMP` | Fim da operação, horário do host; `NULL` em `STARTED` |
| `duration_seconds` | `DOUBLE` | `ended_at - started_at`, em segundos; `NULL` em `STARTED` |
| `queue_wait_seconds` | `DOUBLE` | Espera entre enfileirar e iniciar de fato |
| `dt_execution` | `DATE` | Data do início; eixo de clustering para consultas por dia |
| `window_start` | `DATE` | Data de início da janela processada; `NULL` sem janela |
| `window_end` | `DATE` | Data de fim da janela processada; `NULL` sem janela |
| `status` | `STRING` | `STARTED`, `PUBLISHED`, `NOOP`, `RETRY`, `FAILED`, `EMPTY_SOURCE`, `SKIPPED` ou `OPTIMIZED` |
| `attempts` | `INT` | Número da tentativa; 1 na primeira |
| `error_type` | `STRING` | Classe da exceção; `NULL` em sucesso |
| `error_message` | `STRING` | Mensagem ou stack, até 2000 caracteres; `NULL` em sucesso |
| `detail` | `STRING` | Mensagem de resultado legível |
| `rows_source` | `BIGINT` | Linhas lidas da origem |
| `rows_inserted` | `BIGINT` | Linhas inseridas no destino |
| `rows_updated` | `BIGINT` | Linhas atualizadas no destino |
| `job_run_id` | `STRING` | Run do Databricks Jobs |
| `task_run_id` | `STRING` | Run da task no Databricks Jobs |
| `orchestrator_run_id` | `STRING` | Run do Azure Data Factory que disparou a execução |
| `notebook_path` | `STRING` | Caminho do notebook no workspace |
| `metrics_json` | `STRING` | JSON livre por camada (`read_path`, `predicates`, `throughput`, `execution_mode`) |
| `status_detail` | `STRING` | Refinamento do status na camada: `FIRST_LOAD`, `REJECTED`, `SKIPPED_PREFLIGHT_NOOP`, `SKIPPED_EMPTY_WINDOW` |
| `window_start_at` | `TIMESTAMP` | Instante exato de início da janela; `NULL` sem janela |
| `window_end_at` | `TIMESTAMP` | Instante exato de fim da janela; `NULL` sem janela |

`STARTED` é o único status não terminal e sempre tem `event_seq = 1`. Para o resultado final de uma execução, filtre `event_seq > 1`.

```sql
-- Resultado final das cargas de hoje, por tabela
SELECT target_table, layer, status, status_detail, duration_seconds, rows_inserted, rows_updated
  FROM <env>_it.corp_raw.tbl_dbr_it_log_execucao_pipeline
 WHERE dt_execution = current_date()
   AND event_seq > 1;
```
