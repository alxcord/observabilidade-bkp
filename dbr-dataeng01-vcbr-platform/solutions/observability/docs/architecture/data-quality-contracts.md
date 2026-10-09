# Contratos de dados

## Convenções e ciclo de vida

- Catálogos: `dev_governance` e `prd_governance`, resolvidos pela biblioteca corporativa.
- Schemas: `corp_raw`, `corp_cleansed` e `corp_curated`.
- Tabelas usam `tbl_`; views usam `vw_`; origens usam `lga`, `dbr` e `pbi` somente em raw e cleansed.
- Raw preserva a origem com metadados mínimos de ingestão.
- Cleansed normaliza, consolida segmentos temporais e elimina duplicidade sem cruzar fontes.
- Curated correlaciona fontes, interpreta configuração e calcula saúde, eventos e incidentes.
- Definições, inventários, tags e relações de snapshot usam SCD Tipo 2 com `valid_from`, `valid_to` e `is_current`.
- Coletas usam horário original em UTC, watermark, sobreposição e chaves determinísticas; ausência só encerra uma versão após extração completa bem-sucedida e, quando aplicável, período de carência.
- Retenção histórica inicial: dois anos.

## Objetos raw e cleansed prioritários

| Objeto | Origem e grão | Tratamento principal |
|---|---|---|
| `tbl_lga_it_adf_execucao_pipeline` | `ADFPipelineRun`; uma execução por `RunId` | Preservar início, término, duração, estado, trigger e erro sanitizado |
| `tbl_lga_it_adf_execucao_atividade` | `ADFActivityRun`; atividade e tentativa | Preservar `ActivityRunId`, `PipelineRunId`, tipo, duração e erro; sustenta a correlação |
| `tbl_lga_it_adf_execucao_gatilho` | `ADFTriggerRun`; uma execução por `TriggerRunId` | Preservar agendamento, disparo, pipelines acionados e resultado |
| `tbl_dbr_it_definicao_job` | `system.lakeflow.jobs`; job versionado | SCD2 por `account_id`, `workspace_id`, `job_id` |
| `tbl_dbr_it_definicao_tarefa` | `system.lakeflow.job_tasks`; tarefa versionada | SCD2 por conta, workspace, job e `task_key`; preservar dependências |
| `tbl_dbr_it_execucao_job` | `job_run_timeline`; execução | Consolidar segmentos sem misturar tentativas; preservar `run_name`, estado, compute e duração |
| `tbl_dbr_it_execucao_tarefa` | `job_task_run_timeline`; tarefa e tentativa | Consolidar segmentos e manter jobs multitarefa separáveis |
| `tbl_dbr_it_tabela` | `information_schema.tables`; snapshot diário | SCD2 do inventário; `last_altered` não serve como freshness de dados |
| `tbl_dbr_it_tag_tabela` | `information_schema.table_tags`; uma tag por tabela | SCD2 para contexto histórico de domínio, criticidade, SLA e escopo |
| `tbl_dbr_it_coluna` | `information_schema.columns`; coluna monitorada | SCD2 para detectar inclusão, remoção, tipo, nulabilidade, posição e comentário |
| `tbl_dbr_it_tag_coluna` | `information_schema.column_tags`; tag por coluna | SCD2 para classificação, sensibilidade e tipo semântico |
| `tbl_dbr_it_atualizacao_tabela` | `DESCRIBE DETAIL`; tabela por ciclo | Deduplicar pelo ativo e estado técnico observado; preservar `lastModified`, `sizeInBytes`, `numFiles`, `createdAt`, coleta e run |
| `tbl_dbr_it_regra_qualidade` | YAML resolvido pelo executor; versão de regra | SCD2 por regra e versão explícita ou hash do conteúdo funcional |
| `tbl_dbr_it_resultado_qualidade` | Avaliação de DQ; regra, versão, ativo e avaliação | Normalizar adapters, gerar ID determinístico e separar execução técnica do resultado dos dados |

As execuções devem ser associadas à definição vigente por junção temporal. Mudanças posteriores de nome, dependência ou propriedade não podem reinterpretar o histórico.

## Objetos condicionais

| Objeto | Condição e finalidade |
|---|---|
| `tbl_dbr_it_definicao_pipeline` | Criar quando houver Lakeflow Declarative Pipelines relevantes; SCD2 da definição |
| `tbl_dbr_it_atualizacao_pipeline` | Criar junto da definição; consolidar segmentos por `pipeline_id` e `update_id` |
| `tbl_dbr_it_lineage_tabela` | Eventos de `system.access.table_lineage`, filtrados por workspace VC e deduplicados por hash |
| `tbl_pbi_it_execucao_scan` | PoC Power BI; comprovar a completude do scan antes de interpretar ausências |
| `tbl_pbi_it_metadado_artefato` | PoC Power BI; SCD2 de workspace, modelo semântico, relatório e dataflow |
| `tbl_pbi_it_lineage` | PoC Power BI; SCD2 das arestas devolvidas pelo scan, sem inferência por similaridade de nome |

O lineage de coluna e uma cópia local da view corporativa de workspaces não pertencem ao modelo mínimo.

## Objetos curated

| Objeto | Responsabilidade |
|---|---|
| `tbl_it_processo_dado` | Cadastro SCD2 unificado de pipelines ADF, jobs Databricks e pipelines Lakeflow |
| `tbl_it_ativo_dado` | Identidade única de tabelas, views e futuros ativos externos |
| `tbl_it_expectativa_execucao` | Agendas, periodicidade, SLA e tolerância aprovados |
| `tbl_it_atualizacao_ativo` | Freshness e evolução física enriquecidas com processo e expectativa |
| `tbl_it_mudanca_coluna` | Diferenças temporais de schema e tags, com tipo e severidade |
| `tbl_it_evento_tecnico` | Condição alertável antes da decisão de comunicação |
| `tbl_it_avaliacao_notificacao` | Elegibilidade, supressão, envio e erro de entrega; memória idempotente |
| `tbl_it_incidente` | Agrupamento de eventos que representam o mesmo problema |
| `tbl_it_relacao_ativo_processo` | Relações de leitura e escrita entre processo e ativo |
| `tbl_it_relacao_lineage` | Grafo federado com evidência nativa, coletada ou declarada |

Views prioritárias: `vw_it_correlacao_execucao`, `vw_it_saude_processo`, `vw_it_incidente`, `vw_it_freshness`, `vw_it_volume` e `vw_it_score_qualidade`. Views condicionais: `vw_it_cobertura_lineage` e `vw_it_health_score`. Uma view só deve ser materializada quando desempenho, custo, correlação manual ou auditoria de transições justificar duplicação.

## Contrato canônico de Data Quality

Uma avaliação representa uma regra, uma versão, um ativo e uma execução específica. Campos mínimos:

| Grupo | Campos |
|---|---|
| Identidade | `quality_result_id`, `quality_rule_id`, `quality_rule_version`, `asset_id`, `rule_scope`, `column_names` |
| Execução | `execution_id`, `adf_pipeline_run_id`, `adf_activity_run_id`, `databricks_job_run_id`, `databricks_task_run_id`, `correlation_id` |
| Tempo | `source_evaluation_id`, `evaluation_started_at`, `evaluated_at`, `evaluation_window_start`, `evaluation_window_end`, `ingested_at` |
| Estado | `evaluation_status`, `quality_outcome`, `severity`, `is_blocking`, `action` |
| Métricas | `records_evaluated`, `records_passed`, `records_failed`, `failure_rate`, `threshold_applied`, `rule_score` |
| Origem e diagnóstico | `source_framework`, `framework_run_id`, `error_message`, `quarantine_reference` |

`evaluation_status` aceita `SUCCESS`, `ERROR` ou `NOT_EXECUTED`; `quality_outcome` aceita `PASS`, `WARN`, `FAIL` ou `UNKNOWN`. Um erro do executor nunca pode ser convertido em falha de qualidade.

`quality_result_id` é um SHA-256 da identidade lógica da avaliação. Preferir `source_evaluation_id`; na ausência, usar `execution_id` e `evaluated_at`; por fim, usar janela, ativo e regra. `ingested_at` não participa da chave. O writer executa `MERGE` pelo ID e preserva os componentes para auditoria.

Adapters de Lakeflow Expectations, Data Quality Monitoring e SQL customizado retornam o mesmo DataFrame canônico. O fluxo comum valida, enriquece e persiste; adapters não gravam diretamente. Registros inválidos ficam em quarentena protegida, e as Observability Tables guardam somente métricas e uma referência opcional.

## Freshness e volume

`DESCRIBE DETAIL` é executado apenas para ativos monitorados e produz:

- `lastModified`: última transação do Delta log, usada como freshness técnica padrão;
- `sizeInBytes`: tamanho físico observado;
- `numFiles`: quantidade de arquivos físicos;
- `createdAt`: criação da tabela.

O benchmark de referência mediu 181 tabelas em 41,33 segundos, zero erros e compute ativo. O orçamento reserva um minuto por ciclo para a consulta e contabiliza separadamente inicialização e escrita. O piloto deve repetir o teste no workflow de produção.

`lastModified` também pode avançar por `OPTIMIZE`, `VACUUM` e alteração de propriedade. Como essas manutenções separadas são raras no escopo inicial, o MVP aceita a aproximação. Ativos incompatíveis com ela usam `DESCRIBE HISTORY` sob demanda para distinguir operações de dados das de manutenção.

Variações de `sizeInBytes` e `numFiles` medem evolução física, não registros. Uma contagem de linhas só é incorporada quando já produzida por pipeline, métrica de escrita ou regra de qualidade.

## Tags e metadados

Tags mínimas consumidas: `vc_data_domain`, `vc_data_owner`, `vc_data_steward` (herdadas do schema), `vc_source_system`, `vc_source_object`, `vc_criticality`, `vc_refresh_frequency`, `vc_freshness_sla`, `observability_enabled` e `data_semantic_type`.

Regras detalhadas de qualidade não ficam em tags. O YAML ou repositório registra ID, nome, expressão, colunas, limite, severidade, ação (`warn`, `drop`, `fail`), responsável funcional, versão e vigência.
