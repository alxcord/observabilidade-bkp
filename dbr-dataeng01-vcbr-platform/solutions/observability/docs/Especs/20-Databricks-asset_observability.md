# 20 - Databricks - asset_observability

| Item | Valor |
|---|---|
| Tipo de componente | Job Databricks (`spark_python_task`), definido em `resources/jobs.yml` (job `asset_observability`) |
| Nome do job no workspace | `vc-observability-asset-observability`, igual em DEV e PRD |
| Repositório | `dbr-dataeng01-vcbr-platform` (este repositório) |
| Arquivo desta spec | `solutions/observability/docs/Especs/20-Databricks-asset_observability.md` |
| Código | `solutions/observability/src/vc_observability/asset_observability/asset_observability.py`, `constants.py` |
| Cadência | Sem agenda no bundle: disparado sob demanda, pelo ADF em produção. A cadência diária da versão anterior depende agora desse gatilho |
| Parâmetros | `--environment <dev\|prd\|prod>` (o bundle passa `${bundle.target}`; sem ele, detecta pelo org ID do workspace), `--catalog <catálogo>` (opcional, repetível), `--schema <schema>` (opcional, repetível), `--monitored-assets-path <arquivo>` (o bundle passa `${workspace.file_path}/config/shared/assets/monitored-assets.yaml`; o padrão `config/shared/assets/monitored-assets.yaml`, relativo à pasta da solução, serve à execução local) |
| Dependências | `pyspark>=3.5` e `PyYAML>=6.0` (`requirements.txt`). No job, `PyYAML` entra por `libraries`; o PySpark vem do runtime |
| Especificações relacionadas | [12](12-Job-hub-publisher.md), [22](22-Databricks-schema_observability.md), [40](40-Configuracao-YAML.md) |

> **Versão desta spec.** Descreve `ASSET_OBSERVABILITY_VERSION = "2026-10-09.1"`.

> **Catálogo por ambiente.** As tabelas de destino ficam em `<gov>.corp_cleansed`, onde `<gov>` é `dev_governance` em desenvolvimento e `prd_governance` em produção. O mapeamento está em `GOVERNANCE_CATALOG_BY_ENVIRONMENT` (`constants.py`) e é escolhido pelo `--environment`.

## 1. Objetivo funcional

A cada execução, verificar se cada tabela Delta monitorada foi atualizada dentro do prazo prometido (freshness) e se o número de registros está dentro dos limites esperados (volume). Também registra, só para observação, as métricas de cada commit Delta das tabelas monitoradas. O resultado vira evento na staging; o job nunca fala com o hub.

## 2. Entradas

| De onde | O quê |
|---|---|
| `{catalog}.information_schema.schema_tags` | Tags do schema (herdadas pelas tabelas) |
| `{catalog}.information_schema.table_tags` | Tags das tabelas do schema: `vc_refresh_frequency`, `vc_freshness_sla`, `vc_data_domain`, `vc_criticality` |
| `{catalog}.information_schema.tables` | Lista de ativos: tabelas `MANAGED` ou `EXTERNAL` no formato `DELTA` do schema, com ou sem tag |
| `SHOW CATALOGS` e `SHOW SCHEMAS IN <catalog>` | Descoberta dos catálogos do ambiente (prefixo `dev_` ou `prd_`) e dos schemas (exceto `information_schema` e `default`) |
| YAML de ativos (`--monitored-assets-path`) | Sobrescrita campo a campo das tags (`vc_refresh_frequency`, `vc_freshness_sla`, `vc_data_domain`, `vc_criticality`) e bloco `volume` (seção 5.2) |
| `DESCRIBE DETAIL <tabela>` | `lastModified`, `sizeInBytes`, `numFiles` |
| `SELECT COUNT(*) FROM <tabela>` | Contagem de registros |
| `DESCRIBE HISTORY <tabela>` | Commits Delta e `operationMetrics` |
| `tbl_dbr_it_asset_observability` | Última observação do ativo (referência de volume) |
| `tbl_dbr_it_asset_commit_metrics` | Maior `delta_version` já registrada por ativo (watermark de commits) |

## 3. Saídas (todas em `<gov>.corp_cleansed`, modo append)

As três tabelas são criadas por DDL no início da execução (`ensure_destination_tables()`), quando ainda não existem, com comentários de coluna e as tags de governança de `GOVERNANCE_TABLE_TAGS` mais `vc_medallion_layer`. A `PRIMARY KEY` é informativa (`NOT ENFORCED`): documenta o grão, não garante unicidade.

| Tabela | Grão | Colunas |
|---|---|---|
| `tbl_dbr_it_asset_observability` | Um registro por ativo por execução | `asset STRING, updated_at TIMESTAMP, size_bytes BIGINT, file_count BIGINT, row_count BIGINT, observed_at TIMESTAMP`. PK `(asset, observed_at)` |
| `tbl_dbr_it_asset_commit_metrics` | Um registro por commit (ou um agregado) | `asset STRING, delta_version BIGINT, commit_timestamp TIMESTAMP, operation STRING, commits_collapsed BIGINT, has_gap BOOLEAN, observed_at TIMESTAMP` e as métricas `num_output_rows, num_target_rows_inserted, num_target_rows_updated, num_target_rows_deleted, num_target_rows_copied, num_source_rows, num_deleted_rows, num_updated_rows, num_added_files, num_removed_files, num_added_bytes, num_removed_bytes` (todas `BIGINT`, nulas quando a operação não publica). PK `(asset, delta_version)` |
| `tbl_dbr_it_observability_events` | Dois eventos por ativo com expectativa avaliável por execução (freshness e volume) | Contrato da especificação 12; `source_event_id` = SHA-256 em hexadecimal de fonte, ativo e instante da execução (seção 5.5), `sent_at` nulo. PK `(source_event_id)` |

Cada gravação passa `schema=` explícito para `createDataFrame` (`OBSERVATIONS_SCHEMA`, `COMMIT_METRICS_SCHEMA`, `EVENTS_SCHEMA`). Os valores de cada linha são montados pelo nome da coluna, na ordem do schema (`_rows_in_schema_order()`), porque o Spark associa por posição: assim uma reordenação de campos não troca colunas do mesmo tipo sem erro.

## 4. Fluxo de processamento

1. Lê o YAML de ativos. Arquivo ausente não é erro e não apaga nada.
2. Garante as três tabelas de destino do ambiente (cria por DDL as que faltarem), antes de varrer qualquer catálogo.
3. Descobre os catálogos do ambiente (prefixo `dev_` ou `prd_`), filtrados por `--catalog` se informado. Para cada um, os schemas: os de `--schema` ou todos, exceto os de sistema.
4. Para cada schema, lê as tags do schema e das tabelas (com herança) e lista as tabelas Delta do schema. Soma os ativos do YAML que começam por `<catalog>.<schema>.`. Exclui as três tabelas de destino do próprio job (que moram em `<gov>.corp_cleansed` e levam `vc_refresh_frequency` pelas tags de bootstrap) e as tabelas internas de pipeline (prefixo `__` e `event_log_<uuid>`). O log lista as excluídas. Mescla tags e YAML campo a campo (YAML vence).
5. Todo ativo é observado. Tem expectativa avaliável (gera eventos de freshness e de volume) quem tem `vc_refresh_frequency` válida, qualquer que seja a frequência. Os demais são coletados, sem eventos:
   - sem `vc_refresh_frequency`: ninguém prometeu quando atualizaria;
   - frequência fora do formato: erro em log, sem derrubar a varredura.
6. Para cada schema, lê em uma consulta cada o watermark de commits e a observação anterior. Depois, em paralelo (pool de 8 threads, `ASSET_COLLECTION_WORKERS`), por ativo:
   - lê os commits novos desde o watermark (falha aqui não impede o resto);
   - executa `DESCRIBE DETAIL` e `COUNT(*)`. Falha no `COUNT` deixa `row_count` nulo; falha no `DESCRIBE` grava uma linha de tentativa (métricas zeradas, `updated_at` nulo) e o ativo não é avaliado;
   - avalia freshness e volume, se houver expectativa.
7. Grava observações, eventos e métricas de commit de cada schema. Falha de um schema ou catálogo é registrada e o job segue.
8. Aplica a retenção do log de commits (seção 5.4).
9. Ao fim, se houve falhas (inclusive na retenção), o job termina com erro listando quais.

## 5. Regras de negócio

### 5.1 Freshness

O prazo é `vc_freshness_sla` se existir, senão `vc_refresh_frequency`, no formato `<número><unidade>` com unidade `min`, `h`, `d`, `w`, `m` (30 dias) ou `y` (365 dias). O cálculo é em minutos, para que `5min` não vire zero horas.

`FAIL` se `observed_at − lastModified` for maior que o prazo, com `error_code = 'ASSET_STALE'` e descrição legível em pt-BR: "Atualizado há X; o prazo declarado é de Y." `X` e `Y` saem em minutos (abaixo de 90 min), horas com uma casa (abaixo de 48 h) ou dias. Senão `PASS`. `lastModified` também se move com `OPTIMIZE`/`VACUUM`, o que é aceito.

A avaliação vale para qualquer frequência, inclusive menor que o intervalo entre execuções do job: se no momento da leitura uma tabela horária está parada há mais de 1 h, o `FAIL` é verdadeiro. Por ser uma verificação pontual, ela tem três limites:

- só vê o estado no momento da leitura: um atraso que começou e terminou entre duas execuções não aparece;
- o `PASS` que fecha o incidente só vem na execução seguinte, então o incidente pode ficar aberto mais tempo que o atraso real;
- quando o job de carga para, o coletor também avisa a ausência de execução (especificação 10). São dois incidentes verdadeiros para a mesma causa.

### 5.2 Volume

A métrica é `row_count` (padrão) ou `bytes`, se declarado no bloco `volume` do YAML (`metric`). Limites: `row_count_min` (padrão 1), `row_count_max` (opcional), `variation_min_pct` (padrão −40) e `variation_max_pct` (padrão +40). Avaliados em conjunto: viola se qualquer um estourar.

- Contagem igual a zero com `row_count_min` ativo gera `EMPTY_LOAD` ("A tabela está vazia."), sem avaliar variação.
- As demais violações geram `VOLUME_OUT_OF_RANGE`, com os motivos separados por `;` (por exemplo, "variação de -50.0% abaixo do mínimo de -10.0%").
- A variação compara com a observação anterior. Sem anterior (primeira execução) só valem os limites absolutos, e o log diz isso.
- Uma exceção do YAML (`exceptions` com `days_of_month`) sobrescreve, nos dias do mês listados, os campos que declarar; os demais continuam com o padrão. Nunca desliga a regra.
- Sem métrica (contagem falhou), o resultado é `PASS` sem descrição, e o log registra.
- Bloco `volume` mal formado: erro em log e a regra padrão. Não derruba o ativo nem a varredura.

### 5.3 Severidade e domínio

Severidade vem de `vc_criticality`: `CRITICAL` e `HIGH` → `HIGH`, `MEDIUM` → `MEDIUM`, `LOW` → `LOW`, outro valor ou ausência → `MEDIUM`. Domínio vem de `vc_data_domain` (tag de schema herdada ou de tabela, a da tabela vence), padrão `DEFAULT`.

### 5.4 Métricas de commit (fase 1, só observação)

Watermark por ativo = maior `delta_version` já gravada (vazio = −1, ou seja, a primeira execução pega tudo que o `DESCRIBE HISTORY` ainda tiver). Se a primeira versão disponível no histórico for maior que watermark + 1, a primeira linha nova é marcada `has_gap`. Mais de 500 commits novos (`COMMIT_BATCH_CAP`) geram uma única linha `AGGREGATED` com as somas, e o watermark salta para a última versão. Nenhuma regra consome esta tabela.

Retenção: `COMMIT_METRICS_RETENTION_MONTHS` (13 meses). No fim de cada execução, `purge_commit_metrics()` apaga, com um `MERGE ... WHEN MATCHED THEN DELETE`, os commits cuja data (`commit_timestamp`, ou `observed_at` quando ela não veio) passou do prazo. O commit mais recente de cada ativo nunca é apagado, mesmo vencido: é dele que sai o watermark. Sem ele, um ativo parado há mais tempo que a retenção voltaria ao watermark −1 e o job regravaria os mesmos commits a cada execução. O log informa quantas linhas foram apagadas.

### 5.5 Identificador do evento

`source_event_id` é o SHA-256, em hexadecimal, de `fonte || ativo || instante da execução` (`event_id()`). É um id por execução, não por dia: duas execuções no mesmo dia geram ids diferentes, e o `PASS` da segunda fecha o incidente aberto pela primeira, em vez de ser descartado pelo hub como duplicado. Continua determinístico por linha, o que permite ao hub-publisher reenviar sem duplicar. O hexadecimal respeita o alfabeto que o hub-publisher aceita no id (`[A-Za-z0-9_-]`, especificação 12), que o nome do ativo, com pontos, não respeitaria.

## 6. Permissões e implantação

A identidade que executa o job precisa de:

- `USE CATALOG` nos catálogos `dev_` ou `prd_` do ambiente, `USE SCHEMA` nos schemas varridos, e acesso ao `information_schema` deles (tags e tabelas), além de `SHOW CATALOGS` e `SHOW SCHEMAS`.
- `SELECT` nas tabelas monitoradas (para `DESCRIBE DETAIL`, `DESCRIBE HISTORY` e `COUNT`).
- Em `<gov>.corp_cleansed`: `USE`/`SELECT`/`MODIFY` nas três tabelas de saída. Para a primeira execução de cada ambiente, também `CREATE TABLE` no schema e permissão para aplicar tags (`ALTER TABLE ... SET TAGS`) nas tabelas que o job criar.

O código e o YAML chegam ao workspace pelo deploy do bundle `vc-observability`, que publica a pasta da solução, inclusive `config/`, em `${workspace.file_path}`.

Definição do job: `resources/jobs.yml`, job `asset_observability`. Um único job por target, cluster single node, sem agenda, com uma tarefa `collect_asset_observability` que passa `--environment ${bundle.target}` e `--monitored-assets-path ${workspace.file_path}/config/shared/assets/monitored-assets.yaml`. Sem `--catalog` nem `--schema`, ele varre todos os catálogos do ambiente, e não precisa de um job por catálogo.

## 7. Critérios de aceite

- Uma tabela com `vc_refresh_frequency = 1h` e sem atualização há 3 h gera `FAIL`/`ASSET_STALE`; uma carga horária que deixa a tabela vazia gera `EMPTY_LOAD`.
- Uma tabela com `vc_refresh_frequency = 1d` e sem atualização há 30 h gera `FAIL`/`ASSET_STALE`, que vira incidente no hub depois do hub-publisher. Atualizada, gera `PASS` e fecha o incidente na execução seguinte, mesmo que seja no mesmo dia.
- Uma tabela com queda de 50% e `variation_min_pct = -10` gera `VOLUME_OUT_OF_RANGE`.
- Uma tabela que só herda tags do schema é observada e avaliada.
- Execução em `dev` lê `dev_*` e grava em `dev_governance`; não toca `prd_*` nem `prd_governance`.
- Uma execução sem `--schema` não gera observação nem evento para as três tabelas de destino do próprio job.
- Linhas de `tbl_dbr_it_asset_commit_metrics` com mais de 13 meses são apagadas, exceto a mais recente de cada ativo.
- As mensagens de incidente chegam ao Teams acentuadas.
