# observability-collector

Container Apps Job que executa o ciclo de coleta a cada 15 minutos.

**Estado: ciclo completo escrito e testado — três mecanismos Databricks
(§3.1), sinal ADF (§3.2), rede de apoio (§3.1c), resolução via
`event_resolution.resolve_event()` (§4.3), avaliador de ausência de
execução e job travado (§4.4), notificação (§9) e heartbeat (§12).** O que
ainda falta está listado em "Pendências" abaixo, e nenhum item ali bloqueia
os demais — cada um tem seu próprio try/except em `collector/__main__.py`.

## Módulos

| Módulo | Responsabilidade |
|---|---|
| `collector/natural_key.py` | Identidade estável de uma execução Databricks e domínio a partir do ADF. 21 testes. |
| `collector/databricks.py` | Os três passos do ciclo (§3.1): reconciliar ativos, fechar execuções longas por `runs/get`, varrer concluídos por janela de `start_time`. |
| `collector/adf.py` | Sinal primário do ADF: consulta a `ADFActivityRun` no Log Analytics, watermark sobre `TimeGenerated`, dedup por `ActivityRunId`. A correlação do incidente é a chave estável `<factory>/<pipeline>/<atividade>`; o `ActivityRunId` vai em `correlation_ref`. |
| `collector/adf_runs.py` | Pipeline run (`ADFPipelineRun`) e trigger run (`ADFTriggerRun`): pipeline falho só abre incidente se nenhuma atividade da execução falhou, após uma carência de ingestão; `Cancelled` só é registrado. Chave estável por pipeline/trigger, então o `PASS` seguinte fecha. Ver `doc/10`. |
| `collector/integration_runtime.py` | Disponibilidade de nós do Self-Hosted IR pela API de métricas do Azure Monitor (série dividida pela dimensão do IR), por IR cadastrado em `tbl_cfg_integration_runtimes`. Um incidente por (ADF, IR), com `OK`/`PARTIAL`/`DOWN`/`NO_SIGNAL`. Ver [doc/10](../../doc/10-observability-collector.md). |
| `collector/reconciliation.py` | Rede de apoio (§3.1c): compara o capturado por API com `DatabricksJobs` do Log Analytics; só promove um gap a `tbl_executions` na segunda vez que ele aparece ausente. |
| `collector/expectations.py` | Avaliador de ausência de execução e job travado (§4.4): lê `tbl_cfg_execution_expectations` já normalizada, sem interpretar cron. Roda depois de Databricks/ADF, para já ter as observações do ciclo corrente gravadas. |
| `collector/holidays.py` | Feriados nacionais do Brasil (calculado, sem tabela), usado por `expectations.py` quando `observes_national_holidays = 0`. |
| `collector/resolution.py` | Reexporta `resolve_event()` (de `event_resolution.py`, compartilhado com o publicador) e guarda `tbl_executions`/`tbl_ctl_watermark`. |
| `collector/event_resolution.py` | **Não versionado aqui** — copiado de `src/shared/event_resolution.py` no build do Docker (ver Dockerfile). Decide abrir/incrementar/fechar incidente; sem stored procedure. |
| `collector/notification.py` | Consome `vw_pending_notifications`, resolve `kv://<segredo>` no Key Vault e envia ao Teams, marcando `sent_at`. |
| `collector/heartbeat.py` | Grava o sinal de vida ao fim de cada ciclo, via Logs Ingestion API. |
| `collector/__main__.py` | Orquestra os módulos acima, na ordem do documento. Ponto de entrada do Dockerfile (`python -m collector`). |

## Build

`buildContext` deste projeto é a **raiz do repositório**, não esta pasta
(ver `azure-pipelines.yml`) — o Dockerfile copia
`src/shared/event_resolution.py` para dentro de `collector/` antes do
`pip install`, o mesmo mecanismo que `sync-expectations` já usa para
`config/executions/manual-expectations.yaml`. Rodando os testes fora do
Docker (`pytest tests/`), `collector/resolution.py` cai num `except
ImportError` e aponta direto para `src/shared/` — não é preciso copiar
nada à mão para desenvolver ou testar localmente.

## Variáveis de ambiente (Bicep provisiona; ver `infra/main.bicep`)

| Variável | Uso |
|---|---|
| `AZURE_CLIENT_ID` | Managed Identity — obrigatória com mais de uma identidade atribuída ao host (§6). |
| `SQL_SERVER_FQDN`, `SQL_DATABASE` | Conexão pyodbc ao hub. |
| `LOG_ANALYTICS_WORKSPACE_ID` | Sinal ADF e rede de apoio do Databricks. Sem ela, os dois não rodam naquele ciclo (log de aviso, não erro). |
| `KEY_VAULT_URI` | Resolução de `tbl_cfg_alert_routing.destination` (`kv://<segredo>`). Sem ela, notificação não roda. |
| `LOG_ANALYTICS_INGESTION_ENDPOINT`, `LOG_ANALYTICS_DCR_IMMUTABLE_ID` | Heartbeat (§12), via Logs Ingestion API. Sem os dois, o heartbeat só loga um aviso — nunca derruba o ciclo. |

## Pendências

- **Schema de `DatabricksJobs` no Log Analytics não verificado em dado
  real** — a consulta em `reconciliation.py` assume o schema documentado
  publicamente para audit logs do Databricks (`ActionName`, `RequestParams`
  como JSON). Validar contra ~20 execuções reais antes de depender disto em
  produção (item 8 do Apêndice C).
- **DCE/DCR do heartbeat são pré-requisito externo**, fora do Bicep deste
  projeto — mesma fronteira que já vale para os workspaces Databricks de
  `tbl_cfg_workspaces`. Sem eles, o heartbeat não escreve, mas o ciclo
  inteiro segue normalmente.
- **Testes de corrida em `resolve_event()`** (dois eventos simultâneos na
  mesma `correlation_key`, ADF e Databricks em ordens opostas) continuam
  sendo item de integração contra um Azure SQL real — `src/shared/tests/`
  testa a lógica de decisão com uma conexão fake, mas o comportamento do
  índice único sob concorrência de verdade só um banco real confirma.
- **Canal `email` de `tbl_cfg_alert_routing`** não tem envio implementado —
  só `teams`. Uma notificação roteada para `email` fica pendente na outbox
  e loga um aviso a cada ciclo.
