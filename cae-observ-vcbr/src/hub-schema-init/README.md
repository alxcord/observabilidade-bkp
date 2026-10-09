# hub-schema-init

Container Apps Job **manual** que cria e atualiza o schema do hub (Azure SQL)
com o DDL embutido no próprio código (`schema_definition.py`), sem depender de arquivo `.sql` nem de cliente SQL. Antes
de cada comando olha o banco: cria só a tabela ou o índice que falta, confere a
estrutura das tabelas que já existem e nunca altera nem apaga nada. Especificação completa: [doc/14](../../doc/14-hub-schema-init.md).

```bash
# Simular
az containerapp job start -n hub-schema-init-dev -g RG-ANALYTICS-DEV-VCBR \
  --args "--dry-run"
# Aplicar o schema (sem argumentos)
az containerapp job start -n hub-schema-init-dev -g RG-ANALYTICS-DEV-VCBR
# Schema + usuários de banco e permissões
az containerapp job start -n hub-schema-init-dev -g RG-ANALYTICS-DEV-VCBR \
  --args "--grant-users"
```

Os logs da execução ficam no Log Analytics do Container Apps Environment.

## Testes

```bash
python -m pytest tests
```

## Alterando o schema

O DDL está **só** em `schema_definition.py`; não há cópia em arquivo `.sql`. Para evoluir o schema, acrescente a tabela ou a coluna lá (tabelas em ordem de dependência) e aplique o `ALTER TABLE` correspondente nos bancos já existentes: o job não altera tabela com dados, só cria o que falta e reprova se a estrutura divergir.
