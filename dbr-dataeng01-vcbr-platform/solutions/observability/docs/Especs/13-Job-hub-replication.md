# 13 - Azure Container Apps Job - hub-replication

| Item | Valor |
|---|---|
| Tipo de componente | Azure Container Apps Job, agendado |
| Repositório | `cae-observ-vcbr` (criado, ainda vazio) |
| Arquivo desta spec | `solutions/observability/docs/Especs/13-Job-hub-replication.md` |
| Código | `src/container-apps/hub-replication/hub_replication.py`, `constants.py` |
| Infra | `infra/main.bicep` + `infra/params.json`; esteira `azure-pipelines.yml` |
| Cadência | De hora em hora, minuto 15 (`15 * * * *`) |
| Identidade | `mi-observ-coletor-{dev-,}vcbr` |
| Especificações relacionadas | [01](01-Hub-Azure-SQL.md), [12](12-Job-hub-publisher.md) |

> **Catálogo por ambiente.** O catálogo de governança onde ficam as tabelas e o volume Delta da solução depende do ambiente: `prd_governance` em produção e `dev_governance` em desenvolvimento. Neste documento ele aparece como `<gov>` (por exemplo, `<gov>.corp_cleansed`); troque pelo nome do ambiente ao criar objetos e conceder permissões.

## 1. Objetivo funcional

Copiar para Delta o que o hub precisa manter por mais de 30 dias (execuções e incidentes resolvidos) e avançar o watermark de replicação, que é a condição para a purga do hub. Também alimenta o Power BI executivo (histórico de dois anos). Roda fora do Databricks porque o compute do Databricks não tem rota até o Private Endpoint do banco.

## 2. Entradas

| De onde | O quê | Mecanismo |
|---|---|---|
| Azure SQL | `tbl_ctl_replication` (linha `delta`); `tbl_executions` com `observed_at > watermark`; `tbl_problems` com `closed_at > watermark` e `incident_status = 'RESOLVED'` | `pyodbc`, MI |

## 3. Saídas

| Para onde | O quê | Mecanismo |
|---|---|---|
| Volume `/Volumes/<gov>/corp_raw/hub_replication_staging` | CSV `<tabela>_<watermark anterior>.csv` (com cabeçalho) | Files API `PUT /api/2.0/fs/files/...?overwrite=true` |
| `<gov>.corp_raw.tbl_db_it_hub_executions` e `<gov>.corp_cleansed.tbl_db_it_hub_executions` | Linhas de `tbl_executions` | `COPY INTO ... FILES = ('<arquivo>') FORMAT_OPTIONS ('header'='true','inferSchema'='true')` pela Statement Execution API |
| `<gov>.corp_raw.tbl_db_it_hub_problems` e `<gov>.corp_cleansed.tbl_db_it_hub_problems` | Linhas de `tbl_problems` resolvidos | Idem |
| Azure SQL | `tbl_ctl_replication.replicated_watermark` | `UPDATE` por `pyodbc` |

Colunas replicadas: execuções — `source, scope, natural_key, run_id, run_name, status, result_state, capture_source, observed_at`; incidentes — `problem_id, source, correlation_key, natural_key, key_rule, workspace_id, domain, incident_status, severity, error_code, error_description, opening_run_id, opened_at, closing_run_id, closed_at, occurrence_count`.

## 4. Fluxo de processamento

1. Lê o watermark único (`1900-01-01` se não houver linha).
2. Para cada tabela de origem, lê as linhas acima do watermark. Sem linhas, segue para a próxima.
3. Serializa em CSV (datas em ISO 8601), sobe para o Volume com nome derivado do watermark anterior e executa `COPY INTO` na camada raw e depois na cleansed.
4. Calcula o novo watermark como o **mínimo** entre os maiores timestamps de cada tabela, usando o watermark anterior para a tabela que não trouxe linhas, e nunca retrocede.
5. Se o novo watermark for maior, grava em `tbl_ctl_replication`.

## 5. Regras de negócio

O watermark só avança depois que todas as tabelas confirmaram a carga, para a purga nunca apagar o que não chegou ao Delta. Nenhuma tabela permanentemente vazia pode entrar na lista de origem, porque travaria o mínimo para sempre. A idempotência do retry depende do nome de arquivo determinístico e de o `COPY INTO` não recarregar arquivo já processado.

## 6. Configuração e permissões

Variáveis: `AZURE_CLIENT_ID`, `SQL_SERVER_FQDN`, `SQL_DATABASE`, `DATABRICKS_HOST` (do workspace do ambiente) e `DATABRICKS_WAREHOUSE_ID` (placeholder em `params.json`). Permissões: `db_datareader`/`db_datawriter` no hub; no Databricks, `CAN_USE` no SQL Warehouse, `READ VOLUME` e `WRITE VOLUME` no volume de staging, `USE CATALOG`/`USE SCHEMA` e `MODIFY`/`SELECT` nas quatro tabelas de destino. Volume, tabelas e warehouse precisam existir antes (nenhum artefato os cria).

DDL recomendada para os destinos (repetir para `corp_raw` e `corp_cleansed`):

```sql
CREATE VOLUME IF NOT EXISTS <gov>.corp_raw.hub_replication_staging;

CREATE TABLE IF NOT EXISTS <gov>.corp_raw.tbl_db_it_hub_executions (
  source STRING, scope STRING, natural_key STRING, run_id STRING, run_name STRING,
  status STRING, result_state STRING, capture_source STRING, observed_at TIMESTAMP);

CREATE TABLE IF NOT EXISTS <gov>.corp_raw.tbl_db_it_hub_problems (
  problem_id STRING, source STRING, correlation_key STRING, natural_key STRING,
  key_rule STRING, workspace_id STRING, domain STRING, incident_status STRING,
  severity STRING, error_code STRING, error_description STRING, opening_run_id STRING,
  opened_at TIMESTAMP, closing_run_id STRING, closed_at TIMESTAMP, occurrence_count INT);
```

## 7. Critérios de aceite

Depois de uma execução, as linhas novas do hub aparecem uma única vez nas quatro tabelas Delta e o watermark avança. Reexecutar sem dado novo não duplica nada. Uma falha no `COPY INTO` deixa o watermark onde estava, e a execução seguinte carrega as linhas sem duplicar.
