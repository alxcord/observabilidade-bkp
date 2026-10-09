# 01 — Hub operacional (Azure SQL Database)

O hub é o único lugar onde mora o estado de um incidente. Cada incidente é uma linha de `tbl_problems` com `incident_status` `OPEN` ou `RESOLVED`; abrir, incrementar e fechar são decididos exclusivamente por `resolve_event()` ([02](02-resolucao-de-eventos.md)). O restante do banco alimenta essa decisão (configuração, watermarks, execuções) ou a consome (fila de notificação, views do Power BI). Guarda 30 dias de dado operacional; o histórico longo vai para Delta pela replicação ([13](13-hub-replication.md)).

| Item | Valor |
|---|---|
| Recurso | Servidor `sqlsrv-observ-{dev-,}vcbr`, banco `db-observ-{dev-,}vcbr` (Private Endpoint, **somente** autenticação Entra ID) |
| DDL | Embutido em [schema_definition.py](../src/hub-schema-init/schema_definition.py), aplicado pelo job [14](14-hub-schema-init.md) |
| Escrevem | observability-collector, sync-expectations, hub-publisher, hub-replication (só `tbl_ctl_replication`) e a API de configuração (só `tbl_cfg_*`, repositório próprio) |
| Leem | os mesmos, mais Power BI (DirectQuery nas views) |

## Modelo de dados

Datas em `DATETIMEOFFSET(3)`, UTC. Collation *case-insensitive*; a aplicação normaliza chaves para minúsculas. **Não há stored procedure**: toda regra está em Python.

| Grupo | Tabela | Conteúdo |
|---|---|---|
| Referência / config | `tbl_ref_source` | Origens válidas: `ADF`, `DATABRICKS`, `DATA_QUALITY`, `FRESHNESS`, `VOLUME`, `SCHEMA`, `LINEAGE` |
| | `tbl_cfg_workspaces` | Workspaces Databricks varridos (`workspace_url` completo, com `https://`) |
| | `tbl_cfg_domain_scope` | Escopo técnico (Data Factory, `workspace_id`) → domínio de negócio |
| | `tbl_cfg_alert_routing` | `domain` + `severity` + `channel` → `destination` (`kv://<segredo>`); semeada com rotas `DEFAULT` |
| | `tbl_cfg_integration_runtimes` | Self-Hosted IR monitorado: `data_factory` (ADF **dono** do SHIR; Linked IR não se cadastra), `integration_runtime`, `subscription_id` e `resource_group` (o coletor precisa do id ARM do ADF para a API de métricas), `expected_nodes` (>= 1), `active`; chave `(data_factory, integration_runtime)`. IR fora da tabela não é monitorado |
| | `tbl_cfg_execution_expectations` | Expectativa de execução normalizada; chave `(source, natural_key)` |
| Controle | `tbl_ctl_watermark` | Por fonte: `DBX:<workspace_id>`, `ADF:PipelineRun` (atividade), `ADF:PipelineRunStatus`, `ADF:TriggerRun`, `DBX_LA:reconciliation`. A disponibilidade de nós do IR não tem watermark: vem da API de métricas, que devolve o estado atual |
| | `tbl_ctl_replication` | Linha `delta` com `replicated_watermark` (semeada com `1900-01-01`) |
| | `tbl_ctl_reconciliation_gaps` | Candidatos a lacuna da rede de apoio, entre ciclos |
| Execução / incidente | `tbl_active_executions` | Foto do que a Jobs API diz estar rodando; reconciliada a cada ciclo |
| | `tbl_executions` | Log *append-only* de toda execução observada (`capture_source` = `API` ou `LOG_ANALYTICS`) |
| | `tbl_problems` | O incidente. `correlation_key` identifica o incidente; `natural_key` a origem do sinal |
| | `tbl_technical_events` | N eventos por incidente (FK `ON DELETE CASCADE`); guarda `source_event_id` (idempotência) e `correlation_ref`, o id da execução visto por outra origem, que liga o evento do ADF ao do Databricks da mesma execução |
| | `tbl_notification_outbox` | Intenção de notificar (`OPEN`/`CLOSE`), gravada na mesma transação do incidente; `sent_at` nulo = pendente |

**Índices que são regra de negócio.** `ux_problems_open (correlation_key) WHERE incident_status = 'OPEN'` garante um único incidente aberto por correlação; não pode ser removido nem trocado por índice em `(source, natural_key)`. `ux_events_idempotency (source, source_event_id)` impede que um evento reenviado produza efeito duas vezes.

**Views** (Power BI e API leem só elas, nunca as tabelas): `vw_pending_notifications` (fila do coletor; rota do domínio do incidente, senão `DEFAULT`, sempre na mesma severidade), `vw_open_problems` (só `OPEN`, `open_minutes`, descrição truncada em 300) e `vw_recently_closed_problems` (`RESOLVED` fechados hoje e ontem em UTC, `duration_minutes`).

## Regras gerais

- **Purga assimétrica.** `OPEN` nunca é apagado por idade. `RESOLVED` e execuções só saem com mais de 30 dias **e** abaixo de `tbl_ctl_replication.replicated_watermark`. Filhas saem por cascata.
- Nenhum componente passa `incident_status`; quem decide é `resolve_event()`.

## Permissões

| Identidade | Permissão |
|---|---|
| `mi-observ-coletor-{dev-,}vcbr` (coletor, sync-expectations, hub-publisher, hub-replication) | `db_datareader`, `db_datawriter` |
| `mi-observ-api-{dev-,}vcbr` | `SELECT` nas duas views e em `tbl_ref_source`; `SELECT/INSERT/UPDATE/DELETE` em `tbl_cfg_workspaces`, `tbl_cfg_alert_routing`, `tbl_cfg_execution_expectations`, `tbl_cfg_domain_scope`, `tbl_cfg_integration_runtimes` |
| Grupos `Data Lake - Administrador de dados` e `Data Lake - Sustentacao` | `db_datareader`, `db_datawriter`, `db_ddladmin` |

## Implantação

1. Servidor e banco existem (`terraform/platform/`); quem executa é administrador Entra do servidor.
2. Rodar o job `hub-schema-init` (`--dry-run`, depois sem argumentos, depois `--grant-users`) — ver [14](14-hub-schema-init.md).
3. Cadastrar ao menos um workspace em `tbl_cfg_workspaces` e trocar o `destination` das rotas `DEFAULT` pelo nome real do segredo do webhook.
4. **Semear os watermarks** antes do primeiro ciclo do coletor (`ADF:PipelineRun`, `ADF:PipelineRunStatus`, `ADF:TriggerRun`, `DBX_LA:reconciliation` e um `DBX:<workspace_id>` por workspace) com o instante atual; sem isso o primeiro ciclo reprocessa todo o histórico e abre incidentes de falhas antigas.

## Critérios de aceite

Schema roda em banco vazio; as três views executam; dois `INSERT` com a mesma `correlation_key` `OPEN` falham com erro 2601; `DELETE` em `tbl_problems` remove eventos e outbox filhos; cada identidade tem exatamente a permissão acima.

## Pendências conhecidas

| Gravidade | Descrição |
|---|---|
| Alta | **Purga não implementada** por nenhum componente; o banco cresce indefinidamente. Sugestão: passo diário no hub-replication, após avançar o watermark |
| Alta | Watermarks não semeados (passo 4 acima) |
| Baixa | Rota `DEFAULT` semeada com `kv://obs-webhook-ops`, enquanto `routing.yaml` usa `kv://webhook-dados-plantao`: padronizar |
