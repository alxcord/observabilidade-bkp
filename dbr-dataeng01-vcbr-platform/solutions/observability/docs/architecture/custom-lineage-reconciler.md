# Custom Lineage Reconciler

O reconciliador publica External Metadata e External Lineage no Unity Catalog a partir das tags técnicas das tabelas. Ele pertence ao pacote `vc_observability` e não depende do `vc_extractor_engine`.

## Origem dos metadados

Uma tabela candidata deve informar `vc_source_system` e `vc_source_object`. A origem pode informar seu schema diretamente em `vc_source_object`, no formato `schema.objeto`, ou pela tag `SOURCE_SCHEMA`. Quando nenhuma dessas opções estiver presente, o valor de `--default-source-schema` é utilizado.

A tag opcional `SOURCE_OBJECT_TYPE` aceita `TABLE` ou `VIEW`. Para lineage de colunas, cada coluna de destino pode informar sua origem pela tag `vc_source_column`.

## Reconciliação

O processo descobre as tabelas acessíveis, agrupa objetos que apontam para a mesma origem externa e calcula o estado desejado. Nomes de External Metadata são determinísticos e colisões recebem um sufixo derivado da identidade da origem.

Somente objetos identificados com `managed_by=custom_lineage_reconciler` são atualizados ou removidos. Relações administradas por outras ferramentas são preservadas. Falhas são isoladas por tabela ou escopo, permitindo que o restante do inventário seja analisado.

O modo padrão é `dry-run`: nenhuma alteração é enviada ao Unity Catalog e o plano é impresso como JSON. `--mode apply` efetiva as mudanças e retorna código diferente de zero quando qualquer reconciliação falha. A opção `--apply` continua disponível para compatibilidade com execuções anteriores.

## Código e testes

A implementação fica em [`src/vc_observability/lineage/reconciler.py`](../../src/vc_observability/lineage/reconciler.py) e os testes ficam em [`tests/test_lineage_reconciler.py`](../../tests/test_lineage_reconciler.py). A suíte usa clientes falsos do SDK e cobre descoberta, tags, colisões, ownership, idempotência, dry-run, aplicação e isolamento de falhas sem depender de uma workspace real.

## Execução pelo bundle

O job `custom_lineage_reconciler` é declarado em [`resources/jobs.yml`](../../resources/jobs.yml). Ele instala o wheel da observabilidade e chama o entry point `reconcile_custom_lineage`. O job permanece em `dry-run` por padrão.

Os filtros `lineage_catalog` e `lineage_schema` são variáveis do bundle. Valores vazios não restringem a descoberta. O schema externo padrão é configurado por `lineage_default_source_schema`, inicialmente `3_INTEGRACAO`. Esses valores inicializam os parâmetros `catalog`, `schema` e `default_source_schema` do job e podem ser substituídos em uma execução manual. O parâmetro `mode` inicia como `dry-run` e aceita `apply` somente quando a execução tiver sido revisada.

Antes de executar com `mode=apply`, execute o job em DEV, revise o resumo JSON e confirme na interface do Unity Catalog se as origens, relações e mapeamentos de coluna planejados estão corretos. Produção deve usar o mesmo wheel validado em DEV e uma Service Principal (identidades em [README](../../README.md#identidades-de-cicd)) com as permissões mínimas necessárias para ler tags e administrar External Metadata e External Lineage.
