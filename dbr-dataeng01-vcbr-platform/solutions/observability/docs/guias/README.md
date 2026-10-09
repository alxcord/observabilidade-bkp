# Guias de uso

Guias práticos para quem vai configurar e acompanhar o monitoramento, um por módulo Databricks da solução. As regras completas de cada módulo estão nas [especificações](../Especs/00-Indice.md).

| Guia | Para quê |
|---|---|
| [asset_observability](asset_observability.md) | Monitorar se a tabela foi atualizada no prazo (frequência e SLA) e se o volume está dentro do esperado |
| [data_quality](data_quality.md) | Escrever regras de qualidade por domínio e acompanhar os resultados |
| [schema_observability](schema_observability.md) | Ser avisado quando a estrutura de uma tabela muda de um jeito que quebra quem a consome |
| [custom_lineage_reconciler](custom_lineage_reconciler.md) | Registrar no Unity Catalog de onde vem cada tabela `raw` |

Os três primeiros entregam alertas pelo mesmo caminho: staging Delta, hub-publisher, incidente no hub, mensagem no Teams, Power BI e API. Esse caminho está explicado na seção 6 do guia do asset_observability. As tabelas que os jobs gravam estão em [Tabelas-Databricks.md](../Tabelas-Databricks.md).
