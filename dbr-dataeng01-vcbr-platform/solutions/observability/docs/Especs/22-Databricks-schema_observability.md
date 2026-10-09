# 22 - Databricks - schema_observability

| Item | Valor |
|---|---|
| Tipo de componente | Job Databricks (`spark_python_task`) |
| Repositório | `dbr-dataeng01-vcbr-platform` (este repositório) |
| Arquivo desta spec | `solutions/observability/docs/Especs/22-Databricks-schema_observability.md` |
| Código | `solutions/observability/src/vc_observability/schema_observability/schema_observability.py`, `constants.py` |
| Cadência | Diária |
| Parâmetros | `--catalog` (obrigatório), `--schema` (obrigatório, repetível), `--monitored-assets-path` (padrão `config/shared/assets/monitored-assets.yaml`, relativo à pasta da solução) |
| Especificações relacionadas | [12](12-Job-hub-publisher.md), [20](20-Databricks-asset_observability.md), [40](40-Configuracao-YAML.md) |

> **Catálogo por ambiente.** O catálogo de governança onde ficam as tabelas e o volume Delta da solução depende do ambiente: `prd_governance` em produção e `dev_governance` em desenvolvimento. Neste documento ele aparece como `<gov>` (por exemplo, `<gov>.corp_cleansed`); troque pelo nome do ambiente ao criar objetos e conceder permissões.

## 1. Objetivo funcional

Detectar mudanças de estrutura nas tabelas monitoradas, classificá-las pelo impacto em quem consome e manter um log de auditoria de todas elas. Só mudança grave em tabela com consumidor conhecido gera alerta.

## 2. Entradas

| De onde | O quê |
|---|---|
| `{catalog}.information_schema.schema_tags` e `table_tags` | `vc_data_domain` e `vc_criticality` (não exige `vc_refresh_frequency`) |
| YAML de ativos | `domain`, `criticality` e `consumers` por ativo |
| `{catalog}.information_schema.tables` | Tabelas existentes no schema (para detectar remoção) |
| `{catalog}.information_schema.columns` | `column_name`, `ordinal_position`, `full_data_type`, `is_nullable` |
| `tbl_dbr_it_schema_snapshot` | Snapshot da execução anterior de cada tabela |

## 3. Saídas (`<gov>.corp_cleansed`, append)

| Tabela | Grão | Colunas |
|---|---|---|
| `tbl_dbr_it_schema_snapshot` | Uma linha por coluna por execução | `table, column, ordinal_position INT, data_type STRING, is_nullable BOOLEAN, observed_at TIMESTAMP` |
| `tbl_dbr_it_schema_changes` | Uma linha por mudança detectada | `table, domain, severity, column, change_type, classification, previous_value, current_value, detected_at TIMESTAMP` |
| `tbl_dbr_it_observability_events` | Uma linha por mudança alertável | `source = 'SCHEMA'`, sempre `FAIL`, `source_event_id = 'SCHEMA:<tabela>:<AAAAMMDD>'` |

## 4. Fluxo de processamento

1. Lê o YAML e, por schema, as tags. Ativos = tabelas com tag de tabela, mais as do YAML no mesmo catálogo/schema. YAML vence a tag em domínio e criticidade.
2. Lê as tabelas existentes. Ativo que não existe mais gera mudança `TABLE_DROPPED` (`CRITICA`) e passa ao próximo.
3. Para os demais, lê as colunas atuais e o snapshot anterior (a última `observed_at` daquela tabela). Sem snapshot anterior, só registra o snapshot (primeira execução).
4. Compara coluna a coluna e classifica (seção 5). Monta o evento quando a mudança é alertável.
5. Grava os snapshots, depois as mudanças, depois os eventos.

## 5. Regras de classificação

| Mudança | `change_type` | Classificação |
|---|---|---|
| Coluna nova | `COLUMN_ADDED` | `COMPATIVEL` |
| Coluna removida | `COLUMN_DROPPED` | `INCOMPATIVEL` |
| Tipo alargado com segurança (lista `SAFE_WIDENING`: inteiros para maiores, inteiros para `float`/`double`/`decimal`, `float` → `double`, `date` → `timestamp`) | `TYPE_CHANGED` | `CONTROLADA` |
| Qualquer outra troca de tipo, inclusive mudança de precisão/escala do mesmo tipo | `TYPE_CHANGED` | `INCOMPATIVEL` |
| Coluna passa a `NOT NULL` | `NULLABILITY_TIGHTENED` | `CONTROLADA` |
| Coluna deixa de ser `NOT NULL` | `NULLABILITY_RELAXED` | `COMPATIVEL` |
| Tabela some do `information_schema.tables` | `TABLE_DROPPED` | `CRITICA` |

Alerta (evento `FAIL`) só quando a classificação é `INCOMPATIVEL` ou `CRITICA` **e** o ativo tem `consumers` no YAML. `error_code` é `SCHEMA_CRITICAL_CHANGE` ou `SCHEMA_INCOMPATIBLE_CHANGE`, e a descrição cita a coluna, os valores antes/depois e os consumidores. Severidade e domínio seguem a regra da especificação 20. Este job nunca envia `PASS`: o incidente fica aberto até ser fechado manualmente no banco (limitação aceita).

## 6. Permissões e implantação

`USE`/`SELECT` no `information_schema` dos catálogos monitorados e `MODIFY`/`SELECT` nas três tabelas de saída. Job sugerido igual ao da especificação 20, apontando para `schema_observability.py`, na mesma agenda diária (pode ser uma segunda tarefa do mesmo job).

## 7. Critérios de aceite

Primeira execução grava só snapshot. Remover uma coluna de um ativo com `consumers` gera uma linha `COLUMN_DROPPED`/`INCOMPATIVEL` no log e um incidente `SCHEMA` no hub; acrescentar uma coluna gera só a linha no log.
