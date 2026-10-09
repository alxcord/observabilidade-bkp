# 13 — Job `hub-replication`

Copia para Delta o que o hub precisa manter por mais de 30 dias (execuções e incidentes resolvidos) e avança o watermark de replicação, que é a condição para a purga do hub ([01](01-hub-azure-sql.md)). Também alimenta o Power BI executivo (histórico de dois anos). Roda fora do Databricks porque o compute do Databricks não tem rota até o Private Endpoint do banco. É o espelho do [hub-publisher](12-hub-publisher.md): em vez de escrever no hub, lê o hub.

| Item | Valor |
|---|---|
| Tipo | Azure Container Apps Job, agendado |
| Código | [src/hub-replication/](../src/hub-replication/) (`hub_replication.py`, `constants.py`) |
| Cadência | De hora em hora, minuto 15 (`15 * * * *`) |
| Identidade | `mi-observ-coletor-{dev-,}vcbr` |

> **Catálogo por ambiente.** `<gov>` = `prd_governance` (produção) ou `dev_governance` (desenvolvimento); hoje fixo como `prd_governance` no código (ver pendências).

## Entradas e saídas

| De / Para | O quê | Mecanismo |
|---|---|---|
| Azure SQL (entrada) | `tbl_ctl_replication` (linha `delta`); `tbl_executions` com `observed_at > watermark`; `tbl_problems` com `closed_at > watermark` e `RESOLVED` | `pyodbc`, MI |
| Volume `/Volumes/<gov>/corp_raw/hub_replication_staging` | CSV `<tabela>_<watermark anterior>.csv` com cabeçalho | Files API `PUT /api/2.0/fs/files/...?overwrite=true` |
| `<gov>.corp_raw.tbl_db_it_hub_executions` e `<gov>.corp_cleansed...` | Linhas de `tbl_executions` | `COPY INTO` pela Statement Execution API |
| `<gov>.corp_raw.tbl_db_it_hub_problems` e `<gov>.corp_cleansed...` | Linhas de `tbl_problems` resolvidos | Idem |
| Azure SQL (saída) | `tbl_ctl_replication.replicated_watermark` | `UPDATE` |

Colunas replicadas — execuções: `source, scope, natural_key, run_id, run_name, status, result_state, capture_source, observed_at`; incidentes: `problem_id, source, correlation_key, natural_key, key_rule, workspace_id, domain, incident_status, severity, error_code, error_description, opening_run_id, opened_at, closing_run_id, closed_at, occurrence_count`.

## Fluxo

1. Lê o watermark único (`1900-01-01` se não houver linha).
2. Para cada tabela de origem lê as linhas acima do watermark; sem linhas, segue.
3. Serializa em CSV (ISO 8601), sobe para o Volume e executa `COPY INTO` na camada raw e depois na cleansed.
4. Novo watermark = **mínimo** entre os maiores timestamps de cada tabela (usa o watermark anterior para a que não trouxe linhas); nunca retrocede.
5. Se maior que o atual, grava em `tbl_ctl_replication`.

## Regras de negócio

O watermark só avança depois que **todas** as tabelas confirmaram a carga, para a purga nunca apagar o que não chegou ao Delta. Tabela permanentemente vazia não pode estar na lista de origem (travaria o mínimo). A idempotência do retry depende de nome de arquivo determinístico e de o `COPY INTO` não recarregar arquivo já processado — é aí que está o principal defeito (abaixo).

## Configuração e permissões

Variáveis: `AZURE_CLIENT_ID`, `SQL_SERVER_FQDN`, `SQL_DATABASE`, `DATABRICKS_HOST`, `DATABRICKS_WAREHOUSE_ID` (placeholder em `params.json`). Permissões: `db_datareader`/`db_datawriter` no hub; no Databricks, `CAN_USE` no SQL Warehouse, `READ VOLUME`/`WRITE VOLUME` no volume, `USE CATALOG`/`USE SCHEMA` e `MODIFY`/`SELECT` nas quatro tabelas. Volume, tabelas e warehouse precisam existir antes (nenhum artefato os cria). DDL recomendada (repetir para `corp_raw` e `corp_cleansed`):

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

## Critérios de aceite

Após uma execução, as linhas novas aparecem uma única vez nas quatro tabelas Delta e o watermark avança. Reexecutar sem dado novo não duplica. Falha no `COPY INTO` deixa o watermark onde estava e a execução seguinte carrega sem duplicar.

## Pendências conhecidas

| Gravidade | Descrição | Direção |
|---|---|---|
| Crítica | **Perda de linhas.** Quando uma tabela não traz linha nova, o watermark não avança; na hora seguinte o job grava o **mesmo nome de arquivo** com mais linhas e o `COPY INTO` o pula (já foi carregado, "mesmo que modificado"). As linhas novas nunca chegam ao Delta e depois são purgadas | Watermark **por tabela** (`delta:tbl_executions`, `delta:tbl_problems`) e maior timestamp do lote no nome do arquivo; alternativa: tabela temporária + `MERGE` por chave |
| Crítica | Tabelas de destino não existem e `COPY INTO` exige tabela; a primeira execução falha | Criar com a DDL acima; não depender de `inferSchema` |
| Crítica | Estágios `deployment` do template não fazem checkout (ver [10](10-observability-collector.md)) | `- checkout: self` |
| Alta | `tbl_executions.observed_at` é o **início** da execução, não a gravação: run longo gravado depois de o watermark passar do início nunca é replicado | Coluna `inserted_at DEFAULT SYSDATETIMEOFFSET()` como coluna de watermark; idem em `tbl_problems` |
| Alta | Purga do hub não executada por ninguém ([01](01-hub-azure-sql.md)) | Ver 01 |
| Alta | Catálogo fixo como `prd_governance` | Variável de ambiente por ambiente |
| Média | Raw e cleansed recebem cópias idênticas; cleansed não deduplica nem tipa | Definir o papel da cleansed (ex.: `MERGE` por `problem_id`/`run_id`) |
| Média | Custo do SQL Warehouse acordando 24 vezes por dia não medido | Medir no primeiro mês |
