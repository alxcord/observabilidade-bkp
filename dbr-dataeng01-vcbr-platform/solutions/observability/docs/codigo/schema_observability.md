# schema_observability

Coletor de mudança de estrutura (schema) dos ativos monitorados: o processo de "copiar as system tables para uma tabela de log" previsto no Cenário 3 (doc de referência, §9 e §15.3). Ele segue o mesmo padrão dos demais jobs Databricks da solução ([jobs-databricks.md](jobs-databricks.md)). As regras completas estão na [especificação 22](../Especs/22-Databricks-schema_observability.md).

Código: `solutions/observability/src/vc_observability/schema_observability/`.

## O que faz

Uma vez por dia, para cada tabela monitorada:

1. lê `{catalog}.information_schema.columns`, metadado de catálogo síncrono, sem a latência que descartou o caminho de alerta baseado nas System Tables (ver Cenário 3);
2. compara com o snapshot gravado na execução anterior;
3. classifica cada mudança em **compatível**, **controlada**, **incompatível** ou **crítica**;
4. grava toda mudança em `tbl_dbr_it_schema_changes` (o log, que nunca é apagado) e, quando a mudança é incompatível ou crítica **e** o ativo tem consumidor conhecido (`consumers:` em `config/shared/assets/monitored-assets.yaml`), enfileira um evento `FAIL` para o hub.

## Classificação

| Mudança | Classificação |
|---|---|
| Coluna nova | Compatível |
| Coluna removida | Incompatível |
| Tipo alargado de forma segura (`int` → `bigint`, `float` → `double`, ...) | Controlada |
| Tipo estreitado ou trocado para algo não relacionado | Incompatível |
| Mesma precisão/escala do tipo mudando (`decimal(10,2)` → `decimal(5,2)`) | Incompatível: o lado seguro, por não saber a direção |
| Coluna vira `NOT NULL` | Controlada |
| Coluna deixa de ser `NOT NULL` | Compatível |
| Tabela some do `information_schema.tables` | Crítica |

## Limites do desenho

**Não envia `PASS`.** Uma mudança de schema é um evento pontual, não um estado contínuo: uma coluna removida não "volta ao normal" sozinha. O incidente aberto por uma mudança incompatível ou crítica fica `OPEN` até resolução manual. É o mesmo modo de falha aceito no documento principal da Observabilidade para a renomeação de pipeline ADF: um incidente que só fecha por intervenção, com runbook como mitigação.

**Escopo de ativos mais amplo que o freshness.** Ao contrário do `asset_observability`, não exige `vc_refresh_frequency`: uma tabela sem promessa de atualização ainda precisa ter a estrutura vigiada.

## Tabelas Delta

`tbl_dbr_it_schema_snapshot`, `tbl_dbr_it_schema_changes` e a staging compartilhada `tbl_dbr_it_observability_events` estão descritas em [Tabelas-Databricks.md](../Tabelas-Databricks.md), seções 3, 5 e 6.

## Testes

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

Usa stub de `pyspark` e roda em qualquer máquina, sem workspace nem cluster.

## Uso

```bash
python schema_observability.py --catalog prd_logistics --schema corp_curated
```

`--schema` pode ser repetido para varrer mais de um schema na mesma execução.
