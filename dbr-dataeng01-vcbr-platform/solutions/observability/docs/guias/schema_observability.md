# Guia de uso - schema_observability

Este guia mostra como vigiar mudanças de estrutura (colunas e tipos) nas tabelas, como cada mudança é classificada e quando ela vira alerta. As regras completas estão na [especificação 22](../Especs/22-Databricks-schema_observability.md); o funcionamento interno, em [docs/codigo/schema_observability.md](../codigo/schema_observability.md).

## 1. O que o job faz

Uma vez por dia, o job lê as colunas de cada tabela monitorada no `information_schema`, compara com o que gravou na execução anterior e registra **toda** mudança num log de auditoria. Só uma parte das mudanças vira alerta: as que quebram quem consome a tabela, e só quando a tabela tem consumidor conhecido.

A primeira execução para uma tabela só grava a foto das colunas; a comparação começa na segunda.

## 2. Quais tabelas entram

- Toda tabela que tem alguma tag de tabela no Unity Catalog.
- Toda tabela declarada no YAML de ativos (`solutions/observability/config/shared/assets/monitored-assets.yaml`), mesmo sem tag.

Ao contrário do asset_observability, não exige `vc_refresh_frequency`: uma tabela sem promessa de atualização também precisa ter a estrutura vigiada.

## 3. Como ligar o alerta: consumidores

O alerta depende da lista `consumers` no YAML de ativos. Sem consumidor declarado, a mudança vai para o log, mas ninguém é acordado.

```yaml
assets:
  - name: prd_public.corp_cleansed.tbl_bacen_dolar_comercial
    consumers:
      - Power BI - Painel de Custos
      - pipeline curated de câmbio
    business_owner: Controladoria
    domain: finance
    vc_data_domain: finance
    criticality: high
    vc_criticality: high
```

- `consumers` é uma lista de texto livre e aparece na mensagem do alerta. Escreva o que ajuda quem recebe a saber quem será afetado.
- A criticidade define a severidade do alerta, e o domínio classifica o incidente; o destino vem das regras de alerta (tipo de falha `schema`), como no [guia do asset_observability](asset_observability.md), seção 6.3. O schema_observability lê as chaves `domain` e `criticality`, e o asset_observability lê `vc_data_domain` e `vc_criticality`; declare as duas formas ([especificação 40](../Especs/40-Configuracao-YAML.md), seção 4).
- Sem domínio e criticidade no YAML, valem as tags `vc_data_domain` e `vc_criticality` da tabela ou do schema.

A mudança no YAML entra por pull request e vale depois do deploy do bundle.

## 4. Como cada mudança é classificada

| Mudança | `change_type` | Classificação | Gera alerta (com consumidor) |
|---|---|---|---|
| Coluna nova | `COLUMN_ADDED` | `COMPATIVEL` | Não |
| Coluna deixa de ser `NOT NULL` | `NULLABILITY_RELAXED` | `COMPATIVEL` | Não |
| Tipo alargado com segurança (`int` → `bigint`, `float` → `double`, `date` → `timestamp`, ...) | `TYPE_CHANGED` | `CONTROLADA` | Não |
| Coluna passa a `NOT NULL` | `NULLABILITY_TIGHTENED` | `CONTROLADA` | Não |
| Coluna removida | `COLUMN_DROPPED` | `INCOMPATIVEL` | Sim |
| Outra troca de tipo, inclusive precisão ou escala (`decimal(10,2)` → `decimal(5,2)`) | `TYPE_CHANGED` | `INCOMPATIVEL` | Sim |
| Tabela sumiu | `TABLE_DROPPED` | `CRITICA` | Sim |

### Exemplo

A coluna `vl_venda` da tabela do dólar muda de `decimal(18,4)` para `decimal(18,2)`. Na execução seguinte:

1. o log `tbl_dbr_it_schema_changes` ganha uma linha `TYPE_CHANGED`, `INCOMPATIVEL`, com `previous_value = decimal(18,4)` e `current_value = decimal(18,2)`;
2. como a tabela tem `consumers`, entra na staging um evento `FAIL` com `error_code = SCHEMA_INCOMPATIBLE_CHANGE`, citando a coluna, os valores antes e depois e os consumidores;
3. o evento segue para o hub e o Teams pelo mesmo caminho dos outros jobs (seção 6 do [guia do asset_observability](asset_observability.md)).

Se a mesma tabela só ganhasse uma coluna nova, haveria apenas a linha `COLUMN_ADDED` no log.

## 5. Onde a mudança aparece

```sql
-- Mudanças da última semana, as mais graves primeiro
SELECT `table`, `column`, change_type, classification, previous_value, current_value, detected_at
  FROM prd_governance.corp_cleansed.tbl_dbr_it_schema_changes
 WHERE detected_at >= current_timestamp() - INTERVAL 7 DAYS
 ORDER BY CASE classification WHEN 'CRITICA' THEN 1 WHEN 'INCOMPATIVEL' THEN 2
                              WHEN 'CONTROLADA' THEN 3 ELSE 4 END,
          detected_at DESC;
```

`table` e `column` são palavras reservadas em SQL: escreva entre crases.

```sql
-- Estrutura de uma tabela numa execução
SELECT `column`, ordinal_position, data_type, is_nullable, observed_at
  FROM prd_governance.corp_cleansed.tbl_dbr_it_schema_snapshot
 WHERE `table` = 'prd_public.corp_cleansed.tbl_bacen_dolar_comercial'
 ORDER BY observed_at DESC, ordinal_position;
```

As colunas das tabelas estão em [Tabelas-Databricks.md](../Tabelas-Databricks.md), seções 5 e 6.

## 6. Como o incidente fecha

Uma mudança de estrutura é um fato pontual, não um estado que "volta ao normal": por isso o job **nunca envia `PASS`**. O incidente aberto fica `OPEN` até ser fechado manualmente, depois que quem consome a tabela foi avisado e se ajustou. Se a mesma tabela tiver outra mudança grave antes do fechamento, ela soma uma ocorrência no mesmo incidente, sem nova mensagem.
