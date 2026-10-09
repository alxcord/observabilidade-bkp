# Guia de uso - custom_lineage_reconciler

Este guia mostra como registrar no Unity Catalog de onde vem cada tabela `raw` (SAP, BW, Datasphere ou outra origem externa), para que a aba **Lineage** mostre a origem. As regras completas estão na [especificação 23](../Especs/23-Databricks-custom_lineage_reconciler.md); o desenho, em [custom-lineage-reconciler.md](../architecture/custom-lineage-reconciler.md).

## 1. O que o job faz

O job `vc-observability-custom-lineage-reconciler` lê as tags de origem das tabelas e, para cada origem, cria no Unity Catalog:

- um objeto **External Metadata** representando a tabela ou view de origem;
- um relacionamento **External Lineage** da origem para a tabela.

É lineage de documentação: não gera evento, não fala com o hub e não bloqueia carga. O job só altera o que ele mesmo criou (ou o que não tem dono) e preserva o lineage criado por outras ferramentas.

## 2. Como declarar a origem de uma tabela

São duas tags de tabela, obrigatórias para a tabela entrar:

| Tag | O que é | Valores |
|---|---|---|
| `vc_source_system` | Sistema de origem | `ds`, `bw`, `sql`, `postgres`, `api`, `flat_file`, `databricks` |
| `vc_source_object` | Objeto de origem, de preferência como `<schema>.<objeto>` | Texto livre: `3_INTEGRACAO.R_GV_ZVC_CDS_SD_0026_CHKIN` |

```sql
ALTER TABLE prd_logistics.corp_raw.tbl_chkin
SET TAGS (
  'vc_source_system' = 'ds',
  'vc_source_object' = '3_INTEGRACAO.R_GV_ZVC_CDS_SD_0026_CHKIN'
);
```

- O `vc_source_object` é dividido no **último ponto**: o que vem antes é o schema da origem, o que vem depois é o objeto.
- Sem ponto, o schema vem do parâmetro `default_source_schema` do job (padrão `3_INTEGRACAO`).
- O tipo do sistema no External Metadata é `SAP` quando o valor contém `sap` ou é exatamente `datasphere`; os demais valores, inclusive `ds`, são registrados como `OTHER`.

### Opcional: origem de cada coluna

Para o lineage mostrar também de qual coluna de origem vem cada coluna, use a tag de coluna `vc_source_column`:

```sql
ALTER TABLE prd_logistics.corp_raw.tbl_chkin
ALTER COLUMN cd_material SET TAGS ('vc_source_column' = 'MATNR');
```

### Opcional: tipo e schema da origem

Sem a tag `SOURCE_OBJECT_TYPE` (`TABLE` ou `VIEW`), a origem é registrada como `VIEW`. A tag `SOURCE_SCHEMA` informa o schema da origem quando ele não está no `vc_source_object`.

## 3. Como rodar

O job tem quatro parâmetros:

| Parâmetro | Padrão | Para que serve |
|---|---|---|
| `mode` | `dry-run` | `dry-run` só simula; `apply` grava no Unity Catalog |
| `catalog` | vazio (todos os permitidos) | Restringe a um catálogo |
| `schema` | vazio (todos os permitidos) | Restringe a um schema (`schema` ou `catalogo.schema`) |
| `default_source_schema` | `3_INTEGRACAO` | Schema de origem quando `vc_source_object` não tem ponto |

O caminho seguro é sempre o mesmo:

1. **Simule.** Rode o job com `mode = dry-run`, restrito ao catálogo ou schema que você alterou.
2. **Revise o resumo JSON** na saída do run. Para cada tabela aparecem as ações planejadas, como `create_external_metadata` e `create_external_lineage`, e as contagens `created`, `updated`, `unchanged`, `skipped` e `failed`.
3. **Aplique.** Rode de novo com `mode = apply` ("Run now with different parameters" no job).
4. **Rode outra vez**: o resultado esperado é `unchanged`. Rodar de novo não duplica nada.

O job termina com erro se alguma tabela falhou, para a falha não passar despercebida.

## 4. Onde ver o resultado

No **Catalog Explorer**, abra a tabela e vá na aba **Lineage**: a origem externa aparece a montante da tabela, com o mapeamento de colunas quando houver `vc_source_column`.

| Situação no resumo | O que significa | O que fazer |
|---|---|---|
| `skipped` com aviso | Falta o schema da origem: `vc_source_object` sem ponto e sem `default_source_schema` | Qualificar o `vc_source_object` |
| Tabela não aparece | Sem as duas tags obrigatórias | Aplicar `vc_source_system` e `vc_source_object` |
| `failed` | Falta permissão ou houve colisão de nome com um objeto de outra ferramenta | Ver a mensagem da tabela no JSON |

## 5. Quando a tag é retirada

Se você remove as tags de origem de uma tabela, a próxima execução com `mode = apply` remove o lineage que o job tinha criado para ela (ação `delete_orphan_external_lineage`). O lineage de outras ferramentas é mantido.
