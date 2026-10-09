# Jobs Databricks

Os processos Databricks da solução ficam em `solutions/observability/src/vc_observability/`. As regras de cada um estão nas especificações; este documento descreve o padrão que eles compartilham.

| Projeto | O que faz | Disparo | Especificação |
|---|---|---|---|
| `asset_observability/` | Freshness e volume por `DESCRIBE DETAIL` | ADF, sem agenda no bundle | [20](../Especs/20-Databricks-asset_observability.md) |
| `data_quality/` | Executa as regras do YAML de cada domínio e grava os resultados | ADF, uma vez por domínio | [21](../Especs/21-Databricks-data_quality.md) |
| `schema_observability/` | Mudança de estrutura por `INFORMATION_SCHEMA`, classificada e logada | Diário | [22](../Especs/22-Databricks-schema_observability.md) |
| `lineage/` (`custom_lineage_reconciler`) | External Metadata e Lineage no Unity Catalog | 1 a 2 vezes ao dia | [23](../Especs/23-Databricks-custom_lineage_reconciler.md) |

As tabelas que esses jobs gravam estão descritas em [Tabelas-Databricks.md](../Tabelas-Databricks.md).

## O padrão que asset_observability, data_quality e schema_observability seguem

**Nenhum deles fala com o banco do hub.** Todos escrevem em `tbl_dbr_it_observability_events` (staging) e nada mais: nenhum job desta pasta atravessa a rede até o Azure SQL.

Isso não é preferência de organização, é o que impede a observabilidade de derrubar a produção. A biblioteca de qualidade roda **dentro de pipelines de negócio**: se ela dependesse do hub, um banco indisponível ou uma credencial rotacionada passariam a falhar a carga. A camada que existe para detectar falhas viraria causa de falhas.

Como efeito colateral, some também a exigência de Network Connectivity Configuration para alcançar o Private Endpoint do banco a partir de compute serverless e, mais fundamental, some a exigência de o workspace Databricks ter **VNet injection**. Um workspace sem VNet injection roda o compute numa VNet gerenciada pela Microsoft, sem nenhuma rota possível até um Private Endpoint na VNet do cliente: não é configuração faltando, é impossibilidade estrutural, e nenhum job desta pasta depende dela.

**Quem lê essa staging e escreve no hub não é um job Databricks.** O hub-publisher é um Container Apps Job do repositório `cae-observ-vcbr`: lê a staging pela Statement Execution API do Databricks e escreve no hub direto por `pyodbc`, chamado de fora do compute Databricks, nunca de dentro. A replicação do hub para Delta (hub-replication) segue a mesma regra, na direção contrária, e por isso também não está aqui. Ver as especificações [12](../Especs/12-Job-hub-publisher.md) e [13](../Especs/13-Job-hub-replication.md).

**Todo evento carrega `source_event_id` determinístico.** O envio é at-least-once; a idempotência é o que torna o reenvio inofensivo. O formato de cada produtor está na seção 3 de [Tabelas-Databricks.md](../Tabelas-Databricks.md).

**Eventos carregam `PASS` e `FAIL`, não só violações.** O hub precisa dos dois: o `FAIL` abre o incidente, o `PASS` fecha. Enviar apenas as violações faria todo incidente de freshness, volume ou qualidade abrir e nunca fechar.

**Tags e nomes de tabela ficam em `constants.py`,** um por projeto. Se a governança renomear uma tag, é o único arquivo a editar.

## Testes

```bash
cd <projeto> && python -m unittest discover -s tests -p "test_*.py" -v
```

Todos usam stub de `pyspark` e do SDK: rodam em qualquer máquina, sem workspace nem cluster.
