# 12 - Azure Container Apps Job - hub-publisher

| Item | Valor |
|---|---|
| Tipo de componente | Azure Container Apps Job, agendado |
| Repositório | `cae-observ-vcbr` (criado, ainda vazio) |
| Arquivo desta spec | `solutions/observability/docs/Especs/12-Job-hub-publisher.md` |
| Código | `src/container-apps/hub-publisher/hub_publisher.py`, `constants.py` (mais `src/shared/event_resolution.py`, copiado no build) |
| Infra | `infra/main.bicep` + `infra/params.json`; esteira `azure-pipelines.yml` (contexto de build na raiz do repositório) |
| Cadência | De hora em hora, minuto 0 (`0 * * * *`) |
| Identidade | `mi-observ-coletor-{dev-,}vcbr` |
| Especificações relacionadas | [02](02-Resolucao-de-Eventos.md), [20](20-Databricks-asset_observability.md), [21](21-Databricks-data_quality.md), [22](22-Databricks-schema_observability.md) |

> **Catálogo por ambiente.** O catálogo de governança onde ficam as tabelas e o volume Delta da solução depende do ambiente: `prd_governance` em produção e `dev_governance` em desenvolvimento. Neste documento ele aparece como `<gov>` (por exemplo, `<gov>.corp_cleansed`); troque pelo nome do ambiente ao criar objetos e conceder permissões.

## 1. Objetivo funcional

Levar ao hub os eventos que os jobs Databricks de avaliação deixaram na tabela de staging Delta. É a única ponte do Databricks para o hub: os jobs Databricks nunca falam com o Azure SQL, e este job, rodando fora do Databricks, lê a staging pela API REST do Databricks e escreve no banco por `pyodbc`.

## 2. Entradas

| De onde | O quê | Mecanismo | Autenticação |
|---|---|---|---|
| Databricks SQL Warehouse | `<gov>.corp_cleansed.tbl_dbr_it_observability_events`, linhas com `sent_at IS NULL` | Statement Execution API (`POST /api/2.0/sql/statements`, espera síncrona de 50 s e polling a cada 3 s até 300 s) | MI, recurso Databricks |

## 3. Saídas

| Para onde | O quê |
|---|---|
| Azure SQL | `tbl_problems`, `tbl_technical_events`, `tbl_notification_outbox`, via `resolve_event()` |
| Databricks | `UPDATE ... SET sent_at = current_timestamp()` na staging, para os eventos confirmados |

## 4. Contrato da tabela de staging

Colunas lidas, nesta ordem: `source`, `result`, `natural_key`, `domain`, `severity`, `error_code`, `description`, `source_event_id`, `observed_at`; mais `sent_at`, gravada aqui. A tabela é escrita por asset_observability, data_quality e schema_observability, e os três precisam respeitar o mesmo esquema. Recomenda-se criá-la explicitamente:

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

## 5. Fluxo de processamento

1. Obtém um token Databricks e abre a conexão com o hub.
2. Executa `SELECT <colunas> FROM <staging> WHERE sent_at IS NULL ORDER BY observed_at LIMIT 500` (o limite vem de `--limit`, padrão 500). Converte `observed_at` de texto para `datetime` UTC.
3. Para cada evento, chama `resolve_event(source, result, natural_key, severity, error_code, error_description=description, source_event_id, observed_at)`. Erro de conexão (`pyodbc.OperationalError`) interrompe o lote; qualquer outro erro só pula aquele evento, que fica pendente.
4. Executa `UPDATE <staging> SET sent_at = current_timestamp() WHERE source_event_id IN (...)` com os IDs confirmados. Antes valida que nenhum ID tem caractere fora de `[A-Za-z0-9_-]`; se tiver, o lote inteiro falha.

## 6. Regras de negócio

O envio é *at-least-once*: se o processo cair entre o `resolve_event` e o `UPDATE`, o evento volta no ciclo seguinte e `resolve_event` o descarta como `DUPLICADO` pelo `source_event_id`. A ordem é por `observed_at`, para que um `FAIL` antigo nunca seja aplicado depois de um `PASS` mais novo do mesmo ativo.

## 7. Configuração e permissões

| Variável | Origem | Uso |
|---|---|---|
| `AZURE_CLIENT_ID`, `SQL_SERVER_FQDN`, `SQL_DATABASE` | Bicep | Identidade e hub |
| `DATABRICKS_HOST` | Bicep, a partir do workspace `dbr-dataeng01-{dev-,}vcbr` (parâmetros `databricksWorkspaceNameOverride` e `databricksWorkspaceResourceGroup` se estiver em outro RG) | URL do workspace |
| `DATABRICKS_WAREHOUSE_ID` | `params.json` | SQL Warehouse usado nas consultas |

Permissões: MI registrada no workspace, `CAN_USE` no SQL Warehouse, `USE CATALOG`/`USE SCHEMA` em `<gov>.corp_cleansed` e `SELECT`, `MODIFY` na tabela de staging; `db_datareader`/`db_datawriter` no hub. O SQL Warehouse precisa existir (serverless com auto-stop curto recomendado).

## 8. Critérios de aceite

Um evento `FAIL` inserido manualmente na staging vira incidente no hub na execução seguinte e ganha `sent_at`. Rodar de novo não altera nada. Um evento `PASS` para o mesmo ativo fecha o incidente. Com o hub inacessível, nenhuma linha recebe `sent_at`.
