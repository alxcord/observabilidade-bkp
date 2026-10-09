# 23 - Databricks - custom_lineage_reconciler

| Item | Valor |
|---|---|
| Tipo de componente | Job Databricks (`spark_python_task`) ou script local |
| Nome do job no workspace | `vc-observability-custom-lineage-reconciler`, igual em DEV e PRD (job `custom_lineage_reconciler` em `resources/jobs.yml`) |
| Repositório | `dbr-dataeng01-vcbr-platform` (este repositório) |
| Arquivo desta spec | `solutions/observability/docs/Especs/23-Databricks-custom_lineage_reconciler.md` |
| Código | `solutions/observability/src/vc_observability/lineage/reconciler.py` (pacote `vc_observability`, entry point `reconcile_custom_lineage`); testes em `solutions/observability/tests/test_lineage_reconciler.py`. Job em `solutions/observability/resources/jobs.yml` (`custom_lineage_reconciler`, `python_wheel_task`) |
| Cadência | Uma a duas vezes por dia |
| Parâmetros | `--apply` (sem ele, só simula), `--catalog` (repetível), `--schema` (repetível, `schema` ou `catalog.schema`), `--default-source-schema`, `--skip-column-lineage` |
| Dependência | `databricks-sdk==0.106.0` |

## 1. Objetivo funcional

Registrar no Unity Catalog a origem externa (SAP, BW, Datasphere) de cada tabela `raw`, criando um objeto de External Metadata para a origem e um relacionamento de External Lineage da origem para a tabela. É lineage documental: não gera evento, não fala com o hub e não bloqueia carga.

## 2. Entradas

| De onde | O quê | Como |
|---|---|---|
| Unity Catalog | Catálogos, schemas e tabelas (exceto `system`, `samples`, `__databricks_internal` e `information_schema`, a menos que filtrados explicitamente) | `WorkspaceClient().catalogs/schemas/tables.list` |
| Unity Catalog | Tags de tabela `vc_source_system` e `vc_source_object`; tag de coluna `vc_source_column` | `entity_tag_assignments.list` |
| Unity Catalog | External Metadata e relacionamentos de lineage existentes | `external_metadata.get_external_metadata`, `external_lineage.list_external_lineage_relationships` |

## 3. Saídas

| Para onde | O quê |
|---|---|
| Unity Catalog — External Metadata | Um objeto por origem, nome `<sistema>_<schema>_<objeto>` normalizado (com sufixo de hash se colidir), `system_type = SAP` quando o sistema contém `sap` ou é `datasphere`, senão `OTHER`, `entity_type = VIEW`, propriedades `managed_by = custom_lineage_reconciler`, `source_system`, `source_schema`, `source_table` |
| Unity Catalog — External Lineage | Relacionamento origem → tabela, com mapeamento de colunas quando houver `vc_source_column`, propriedades `managed_by`, `relationship_type = external_upstream_to_raw`, `source_external_id` |
| Saída padrão (stdout) | Resumo JSON: modo, tabelas varridas, tabelas com tag, contagens (`created`, `updated`, `unchanged`, `skipped`, `failed`) e o estado desejado por tabela |
| Código de saída | `1` se alguma tabela falhou ou se houve erro geral, `0` caso contrário |

## 4. Fluxo de processamento

1. Percorre catálogos, schemas e tabelas dentro dos filtros. Erro de listagem vira resultado `failed` daquele escopo.
2. Para cada tabela, lê as tags. Tabela sem nenhuma das duas tags obrigatórias não gera origem nem metadado, mas é varrida para remover o lineage gerenciado que ela ainda tenha (seção 8, primeiro item). O mesmo vale para tabela com identidade de origem inválida. Com as tags, monta a identidade da origem: `vc_source_object` qualificado (`schema.objeto`) é dividido no último ponto; sem qualificação, usa `--default-source-schema`; sem nenhum dos dois, a tabela é `skipped` com aviso.
3. Lê as tags de coluna `vc_source_column` (se não houver `--skip-column-lineage`).
4. Agrupa as tabelas pela mesma origem. Para cada origem, decide o nome do External Metadata (reaproveita o existente se for compatível e gerenciado por este processo ou sem dono; senão usa nome com hash; colisão sem identidade compatível vira falha) e cria ou atualiza o objeto.
5. Para cada tabela, compara os relacionamentos upstream existentes: cria o esperado se não existir, atualiza se for gerenciado e estiver diferente, remove relacionamentos gerenciados obsoletos ou duplicados, e preserva (com aviso) os não gerenciados.
6. Em modo simulação, nada é escrito: tudo vira "ação planejada" no JSON.

## 5. Regras de negócio

Só altera objetos marcados como gerenciados por ele ou sem dono. Sem `--apply`, é sempre somente leitura. Não navega na origem nem valida o objeto no SAP.

## 6. Permissões e implantação

A identidade precisa de `USE CATALOG`/`USE SCHEMA` e `BROWSE` (ou `SELECT`) nos catálogos varridos, de privilégio para criar e alterar External Metadata no metastore (`CREATE EXTERNAL METADATA` e ser dono ou ter `MODIFY` nos objetos que criar) e de `MODIFY` nas tabelas-alvo para gravar lineage. Recurso de External Lineage precisa estar habilitado no workspace. No job de exemplo, trocar `{{CUSTOM_LINEAGE_RECONCILER_SCRIPT_PATH}}` pelo caminho do script no Git Folder, rodar primeiro em simulação, revisar o JSON e só então acrescentar `--apply`.

## 7. Critérios de aceite

Em simulação, uma tabela `raw` com `vc_source_system = ds` e `vc_source_object = 3_INTEGRACAO.ZTB_X` aparece com ação `create_external_metadata` e `create_external_lineage`. Com `--apply`, a aba Lineage da tabela mostra a origem externa; rodar de novo resulta em `unchanged`.
