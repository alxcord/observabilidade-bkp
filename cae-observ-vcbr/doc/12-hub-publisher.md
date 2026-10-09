# 12 — Job `hub-publisher`

Leva ao hub os eventos que os jobs Databricks de avaliação (asset_observability, data_quality, schema_observability — fora deste repositório) deixaram na tabela de staging Delta. É a única ponte do Databricks para o hub: os jobs Databricks nunca falam com o Azure SQL; este job, rodando fora do Databricks, lê a staging pela API REST do Databricks e grava no banco por `pyodbc`, chamando `resolve_event()` ([02](02-resolucao-de-eventos.md)) direto. Substituiu o desenho antigo em que o Databricks fazia `POST` numa API (Azure Functions).

| Item | Valor |
|---|---|
| Tipo | Azure Container Apps Job, agendado |
| Código | [src/hub-publisher/](../src/hub-publisher/) (`hub_publisher.py`, `constants.py`) + `src/shared/event_resolution.py` copiado no build |
| Build | Contexto de build na raiz do repositório (Dockerfile com `COPY` a partir de `src/...`); trigger de pipeline também em `src/shared/event_resolution.py` |
| Cadência | De hora em hora, minuto 0 (`0 * * * *`) |
| Identidade | `mi-observ-coletor-{dev-,}vcbr` |

> **Catálogo por ambiente.** O catálogo de governança é `prd_governance` em produção e `dev_governance` em desenvolvimento; abaixo aparece como `<gov>`. Hoje está fixo como `prd_governance` no código (ver pendências).

## Entradas e saídas

| De / Para | O quê | Mecanismo |
|---|---|---|
| Databricks SQL Warehouse | `<gov>.corp_cleansed.tbl_dbr_it_observability_events` onde `sent_at IS NULL` | Statement Execution API (`POST /api/2.0/sql/statements`, espera síncrona 50 s, polling a cada 3 s até 300 s), MI |
| Azure SQL | `tbl_problems`, `tbl_technical_events`, `tbl_notification_outbox` | via `resolve_event()` |
| Databricks | `UPDATE ... SET sent_at = current_timestamp()` para os eventos confirmados | Statement Execution API |

## Contrato da tabela de staging

Colunas lidas, nesta ordem: `source`, `result`, `natural_key`, `domain`, `severity`, `error_code`, `description`, `source_event_id`, `observed_at`; mais `sent_at`, gravada aqui. Escrita por três jobs Databricks que precisam respeitar o mesmo esquema. O código não cria a tabela; criar explicitamente:

```sql
CREATE TABLE IF NOT EXISTS <gov>.corp_cleansed.tbl_dbr_it_observability_events (
  source           STRING    NOT NULL,  -- FRESHNESS | VOLUME | DATA_QUALITY | SCHEMA
  result           STRING    NOT NULL,  -- PASS | FAIL
  natural_key      STRING    NOT NULL,
  domain           STRING,
  severity         STRING,              -- HIGH | MEDIUM | LOW
  error_code       STRING,
  description      STRING,
  source_event_id  STRING    NOT NULL,
  observed_at      TIMESTAMP NOT NULL,
  sent_at          TIMESTAMP
);
```

## Fluxo

1. Obtém token Databricks e abre a conexão com o hub.
2. `SELECT ... WHERE sent_at IS NULL ORDER BY observed_at LIMIT 500` (`--limit`, padrão 500); converte `observed_at` para `datetime` UTC.
3. Para cada evento chama `resolve_event(...)`. `pyodbc.OperationalError` interrompe o lote; outro erro só pula aquele evento, que fica pendente.
4. `UPDATE ... SET sent_at` com os `source_event_id` confirmados. Hoje valida antes que cada ID só tenha `[A-Za-z0-9_-]`; se não, o lote inteiro falha (ver pendências).

## Regras de negócio

- Envio *at-least-once*: se o processo cair entre `resolve_event` e o `UPDATE`, o evento volta no ciclo seguinte e é descartado como `DUPLICADO` pelo `source_event_id`.
- A ordem é por `observed_at`, para que um `FAIL` antigo nunca seja aplicado depois de um `PASS` mais novo do mesmo ativo.
- `domain` é lido mas não repassado.

## Configuração e permissões

| Variável | Uso |
|---|---|
| `AZURE_CLIENT_ID`, `SQL_SERVER_FQDN`, `SQL_DATABASE` | Identidade e hub (Bicep) |
| `DATABRICKS_HOST` | URL do workspace `dbr-dataeng01-{dev-,}vcbr` (Bicep; `databricksWorkspaceNameOverride`/`databricksWorkspaceResourceGroup` se estiver em outro RG) |
| `DATABRICKS_WAREHOUSE_ID` | SQL Warehouse das consultas (placeholder em `params.json`) |

MI registrada no workspace, `CAN_USE` no SQL Warehouse, `USE CATALOG`/`USE SCHEMA` em `<gov>.corp_cleansed`, `SELECT` e `MODIFY` na staging, `db_datareader`/`db_datawriter` no hub. O Warehouse precisa existir (serverless com auto-stop curto recomendado).

## Critérios de aceite

Um `FAIL` inserido na staging vira incidente no hub na execução seguinte e ganha `sent_at`; rodar de novo não altera nada; um `PASS` do mesmo ativo fecha o incidente; com o hub inacessível, nenhuma linha recebe `sent_at`.

## Pendências conhecidas

| Gravidade | Descrição | Direção |
|---|---|---|
| Crítica | Estágios `deployment` do template não fazem checkout (ver [10](10-observability-collector.md)) | `- checkout: self` |
| Crítica | `mark_sent` só aceita `source_event_id` com `[A-Za-z0-9_-]`, mas os produtores geram IDs como `FRESHNESS:prd_x.corp_y.tbl_z:20261005` (com `:` e `.`). O `ValueError` derruba o lote **depois** de `resolve_event` já ter rodado: nada recebe `sent_at`, os 500 mais antigos voltam a cada hora e bloqueiam a fila | Usar parâmetros nomeados da Statement Execution API (`IN (:p0, :p1, ...)`) em vez de concatenar e remover a validação |
| Alta | Sem `scope` nem `domain`, todo incidente de DQ/Freshness/Volume/Schema cai em `DEFAULT` | Passar `domain` ([02](02-resolucao-de-eventos.md)) |
| Alta | Catálogo fixo como `prd_governance` em `constants.py`; em dev leria/gravaria em produção | Ler de variável de ambiente (`GOVERNANCE_CATALOG`) definida por ambiente |
| Baixa | `key_rule` não informado grava o padrão `JOB_ID`, rótulo errado | Passar `key_rule=source` |
| Média | Formato do `TIMESTAMP` devolvido pela Statement Execution API não validado em ambiente real (`_parse_observed_at`) | Testar contra o warehouse real |
| Baixa | Documentação diz que este job e o hub-replication rodam juntos no minuto 0, mas o `params.json` do hub-replication está no minuto 15 | Alinhar |
