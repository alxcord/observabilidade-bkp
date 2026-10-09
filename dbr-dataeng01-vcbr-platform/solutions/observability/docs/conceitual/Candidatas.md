# Soluções candidatas

> **Estado da decisão.** A alternativa "Desenvolvimento interno", descrita em detalhe no documento Cen2 (`Cen2-arquitetura-observabilidade-dados-desenvolvimento-Interno.md`), é uma **hipótese arquitetural**, e não uma decisão corporativa de construir. Este documento a trata como **referência técnica e benchmark de TCO** para comparação com as soluções de mercado, e recomenda, no estado atual da análise, avaliar Datadog como primeira opção de POC. As "decisões já tomadas" registradas no Cen2 são decisões internas a essa hipótese — isto é, como a solução seria construída se a organização optar por esse caminho — e não devem ser lidas como decisão de adoção.

## 1. Objetivo

Este documento consolida as principais alternativas de implementação para a capacidade corporativa de **Observabilidade e Data Quality**, tomando como referência o Escopo e a Solução Conceitual já definidos.

A intenção não é apenas comparar produtos. A decisão precisa considerar dois caminhos possíveis:

1. **desenvolver internamente uma capacidade corporativa**, utilizando componentes de plataforma e padrões abertos; ou
2. **adotar uma solução de mercado**, aproveitando de forma efetiva as capacidades nativas do produto.

A comparação considera o contexto atual predominantemente Azure e Databricks, o modelo Hub/Spoke, a possibilidade de expansão para AWS, o uso corporativo de Control-M e ServiceNow e as capacidades conceituais já definidas para Data Quality, SLOs, lineage, roteamento de suporte, Data Contracts, histórico e observabilidade operacional.

Este documento não pressupõe que uma solução de mercado deva reproduzir fisicamente o modelo conceitual interno. O modelo conceitual estabelece as capacidades e relações que precisam ser preservadas; a implementação pode utilizar conceitos nativos de uma ferramenta quando eles atenderem adequadamente ao requisito.

---

## 2. Premissas utilizadas na avaliação

As alternativas foram avaliadas considerando as seguintes premissas.

### 2.1 Independência tecnológica é um objetivo, não um fim em si mesma

A capacidade deve permitir expansão futura para outras regiões, clouds e tecnologias. Entretanto, a busca por portabilidade não deve levar à construção de uma camada própria para abstrair todas as funcionalidades de uma plataforma adquirida.

Caso seja escolhida uma solução SaaS, espera-se utilizar suas capacidades nativas de monitoramento, SLO, alertas, incidentes, dashboards e Data Observability sempre que elas atenderem aos requisitos corporativos.

Devem permanecer mais independentes da ferramenta os conceitos corporativos que precisam sobreviver a uma eventual troca de tecnologia, entre eles:

- identidade dos ativos;
- ownership;
- `support_team`;
- referências a Data Contracts;
- identificadores de correlação;
- lineage/interoperabilidade quando aplicável;
- histórico relevante para auditoria e indicadores corporativos.

### 2.2 Git/YAML não é requisito obrigatório

As configurações operacionais devem ser estruturadas, auditáveis, versionáveis e automatizáveis, mas podem ser administradas:

- diretamente em uma plataforma SaaS;
- por API/SDK;
- por configuração como código;
- por Git/YAML, JSON ou Terraform;
- por uma plataforma corporativa externa, como Data Contracts;
- por uma combinação desses modelos.

A decisão deve ser feita por categoria de configuração.

### 2.3 Data Contracts continuam sendo externos à Observabilidade

A frente responsável por Data Contracts continua sendo proprietária da definição e governança dos contratos.

A solução de Observabilidade pode:

- consumir referências aos contratos;
- receber violações;
- usar condições contratuais para criar SLOs ou regras operacionais;
- correlacionar contrato, ativo, execução, qualidade, alerta e incidente.

### 2.4 Lineage atual é Nível 1 e Nível 2

O estado-alvo atual requer:

- **Nível 1:** rastreabilidade de execução;
- **Nível 2:** relações upstream/downstream entre datasets.

Análise de impacto downstream e column-level lineage podem ser benefícios adicionais de uma ferramenta, mas não são critérios obrigatórios de aceite desta iniciativa.

### 2.5 Roteamento de suporte é requisito relevante

A solução precisa determinar qual equipe deve tratar uma condição.

O modelo conceitual prevê:

- equipe padrão do processo ou ativo;
- override na regra de Data Quality;
- override no SLO;
- regras de roteamento contextuais;
- separação entre `support_team` e o grupo específico do ITSM.

### 2.6 O MVP de incidentes pode continuar utilizando Control-M

No fluxo atual, pipelines críticos podem ser monitorados pelo Control-M, que utiliza a falha do job para abrir ou manter um incidente no ServiceNow.

Para problemas de Data Quality, SLO e outras inconsistências, podem existir jobs de verificação por equipe que sinalizem ao Control-M a existência de inconsistências ativas.

Uma integração direta com ITSM é uma evolução desejável, mas não precisa bloquear o MVP.

---

## 3. Critérios de comparação

As soluções candidatas serão comparadas principalmente pelos seguintes critérios:

1. aderência ao modelo conceitual corporativo;
2. observabilidade operacional de pipelines e jobs;
3. Data Observability;
4. Data Quality;
5. SLI/SLO/SLA;
6. lineage Nível 1 e Nível 2;
7. integração com Databricks e Unity Catalog;
8. integração com Azure Data Factory;
9. roteamento por equipe de suporte;
10. integração com ServiceNow e processo de incidentes;
11. capacidade multi-cloud;
12. integração com Data Contracts e APIs;
13. histórico e auditoria;
14. tempo para gerar valor;
15. esforço de desenvolvimento;
16. esforço operacional contínuo;
17. lock-in;
18. custo total de propriedade.

---

## 4. Visão resumida das alternativas

| Alternativa | Modelo | Principal característica |
|---|---|---|
| Desenvolvimento interno | Construção interna | Backend próprio orientado a APIs (Container Apps/Kubernetes), PostgreSQL operacional, Event Hub para ADF, System Tables para Databricks, Delta para histórico, Power BI para visualização |
| Datadog Data Observability | SaaS | Observabilidade operacional e de dados integradas |
| DataHub | SaaS ou open source | Metadata platform, catálogo, lineage e observability |
| OpenMetadata | SaaS/managed ou open source | Catálogo, lineage, Data Quality e observability |
| Monte Carlo | SaaS | Plataforma especializada em Data Observability |
| Microsoft Purview | SaaS Azure | Governança, catálogo, lineage e Data Quality |

---

# 5. Desenvolvimento interno

## 5.1 Descrição

O projeto consolidou uma única arquitetura de desenvolvimento interno, descrita em detalhe no documento Cen2 (`Cen2-arquitetura-observabilidade-dados-desenvolvimento-Interno.md`). Versões anteriores deste documento apresentavam duas alternativas internas separadas — uma mais aderente ao ambiente Azure atual e outra orientada a padrões abertos de interoperabilidade — mas essa separação não reflete o que o projeto efetivamente desenhou. Existe apenas uma arquitetura interna candidata, e esta seção a descreve.

A arquitetura combina os dois conjuntos de argumentos que motivavam as antigas alternativas separadas. Do lado da aderência ao ambiente Azure atual, a solução aproveita deliberadamente os componentes de plataforma já disponíveis — Databricks/Unity Catalog, Event Hub, ADLS, Power BI Premium — em vez de introduzir tecnologias novas apenas por preferência arquitetural abstrata. Do lado da portabilidade e dos padrões abertos, o modelo canônico de entidades e eventos é independente de tecnologia, e o OpenLineage é preservado como um padrão possível de interoperabilidade para fontes externas que já o utilizem, em vez de ser adotado como fundamento obrigatório da arquitetura.

## 5.2 Arquitetura

A arquitetura real é híbrida, com um caminho operacional de baixa latência separado do histórico analítico:

```text
Azure Data Factory
      |
      | Diagnostic Settings
      v
   Event Hub  -------------------------> Event Hubs Capture ---> ADLS (raw, 1 mês)
      |
      v
Observability Backend (Container Apps / Kubernetes)
      |
      v
  PostgreSQL
      |  configuração + estado operacional (OPEN/RESOLVED)
      |  + versões de configuração (2 anos)
      |
      | replicação a cada 4 horas
      v
  Delta / Unity Catalog (histórico e analytics, 2 anos)
      |
      v
   Power BI

Databricks System Tables
      |
      | consulta a cada 30 minutos (sem cluster dedicado)
      v
Databricks Collector -----------------> PostgreSQL

Data Quality Engines / Notebooks / Frameworks
      |
      v
   Observability API (POST /dq-results)
      |
      v
   Event Hub  --------------------------> Observability Backend --> PostgreSQL

Control-M
      |
      v
ServiceNow
```

O backend é uma aplicação orientada a APIs, executando em Container Apps ou Kubernetes. O PostgreSQL é a fonte operacional para configuração e estado atual; o Delta é a camada histórica e analítica, alimentada por replicação periódica, não em tempo real. O Event Hub é usado como barramento apenas onde há benefício natural — ADF via Diagnostic Settings e a API de Data Quality — e não como barramento universal obrigatório para todas as fontes. O Databricks é observado principalmente por consulta periódica às System Tables, evitando listener de cluster. O Power BI é o mecanismo de visualização, aproveitando o Power BI Premium já disponível corporativamente; Grafana não faz parte do desenho-base.

A abordagem é ajustada à Solução Conceitual atual: configurações não precisam obrigatoriamente estar em YAML. O PostgreSQL é a fonte autoritativa das configurações operacionais da plataforma, administrada por API/UI; Git/YAML permanece disponível como mecanismo de import/export, bootstrap ou automação em massa, não como fonte de verdade obrigatória.

## 5.3 Papel do Databricks

O Databricks é utilizado para:

- observação de jobs e workflows via System Tables, sem listener nos clusters;
- lineage nativo via Unity Catalog (`system.access.table_lineage`), consumido por referência em vez de reconstruído;
- correlação com ADF através do `run_name`, cujo UUID final corresponde ao `ActivityRunId`;
- processamento periódico da carga analítica (replicação PostgreSQL → Delta);
- histórico corporativo e camada analítica em Delta, com retenção de 2 anos.

## 5.4 Data Quality

As regras de Data Quality são executadas por diferentes frameworks e enviam resultados para uma API HTTP corporativa (`POST /dq-results`), desacoplada por Event Hub:

```text
DQ Rule
   |
   v
DQ Engine / Notebook / Framework
   |
   v
Observability API
   |
   v
Event Hub
   |
   v
Observability Backend / PostgreSQL
```

Isso permite incorporar frameworks diferentes sem mudar o modelo corporativo, e sem exigir que o framework conheça a estrutura interna do PostgreSQL.

## 5.5 Lineage e OpenLineage como padrão de interoperabilidade

A arquitetura não tem como objetivo construir um produto corporativo próprio de lineage. A capacidade obrigatória é a rastreabilidade operacional entre processo, execução, atividade e ativo; quando a plataforma de origem já oferece lineage upstream/downstream — como o Unity Catalog —, a solução consulta ou referencia essa fonte nativa em vez de reconstruir o grafo.

**Marquez não é considerado componente obrigatório nem centro da plataforma.** Ele poderia ser utilizado se houvesse benefício específico como backend de lineage/OpenLineage, mas a solução corporativa não depende de Marquez para conceitos que ultrapassam seu foco natural. Da mesma forma, **não é necessário instalar um listener OpenLineage nos clusters apenas para duplicar informação que o Unity Catalog já fornece adequadamente**. OpenLineage permanece disponível como padrão de interoperabilidade para fontes externas que já o utilizem — Spark fora do Databricks, AWS, Airflow, Glue — sem ser o fundamento nem o nome da arquitetura.

## 5.6 Prós

- Forte aderência ao ambiente atual Azure/Databricks, aproveitando componentes de plataforma já disponíveis (Unity Catalog, Event Hub, Power BI Premium).
- Não exige subir cluster dedicado para monitoramento; a coleta do Databricks usa consulta periódica às System Tables.
- Modelo canônico independente de tecnologia, preparado para expansão a outras clouds e plataformas (AWS, Databricks fora do Azure).
- OpenLineage disponível como padrão de interoperabilidade quando houver benefício concreto, sem se tornar dependência estrutural.
- Maior controle sobre identidade de ativos, routing, correlação e histórico corporativo.
- Boa integração com o MVP atual de Control-M e ServiceNow, com caminho de evolução para integração direta com ITSM.
- `support_team`, Data Contracts e regras corporativas podem ser modelados exatamente conforme a necessidade da organização.
- Implementação incremental possível a partir do desenho já detalhado no Cen2.

## 5.7 Contras

- É efetivamente desenvolvimento de produto interno: exige construir e sustentar adapters, estado de condições, roteamento, dashboards/UX e observabilidade da própria plataforma.
- Data Quality continua exigindo framework e camada de gestão próprios ou complementares.
- Exige versionamento e compatibilidade de schemas, e manutenção contínua quando APIs e schemas de origem mudarem.
- Recursos avançados de anomalia e correlação estatística precisarão ser desenvolvidos ou adquiridos separadamente (ver decisão pendente D2 sobre o escopo do MVP).
- Lineage cross-technology (ADF → Databricks → consumo em Power BI, por exemplo) não é resolvido apenas pelo lineage nativo do Unity Catalog; a cadeia completa depende da rastreabilidade operacional construída pela solução, não de um grafo de lineage único.
- O custo de infraestrutura pode ser baixo, mas o custo de engenharia e sustentação pode ser alto — ver §12 sobre TCO.

## 5.8 Risco principal

O maior risco é subestimar o esforço de transformar um conjunto de componentes e padrões em um produto corporativo confiável, com UX, operação 24x7, upgrade, segurança, troubleshooting e suporte — e, à medida que forem adicionados DQ, SLOs, lineage, routing, Data Contracts e incidentes, deixar a complexidade crescer sem que isso fique evidente no início.

## 5.9 Avaliação

É a referência arquitetural de desenvolvimento interno para comparação com as soluções SaaS. Aproveita deliberadamente os componentes de plataforma já disponíveis no ambiente Azure/Databricks atual, mantendo ao mesmo tempo um modelo canônico independente de tecnologia e a opção de usar OpenLineage como padrão de interoperabilidade quando houver benefício concreto — sem depender de Marquez, sem exigir listener OpenLineage nos clusters, e sem se tornar uma plataforma de lineage própria.

---

# 6. Datadog Data Observability

## 6.1 Descrição

Datadog evoluiu de uma plataforma de infraestrutura/APM para uma plataforma que também oferece **Data Observability**, incluindo Jobs Monitoring, Quality Monitoring e Lineage.

No contexto avaliado, Datadog é particularmente interessante porque aproxima observabilidade de dados da observabilidade operacional e já possui uma integração corporativa madura com ServiceNow.

## 6.2 Arquitetura potencial

```text
Databricks ---------------- native integration --------\
                                                      |
ADF -------- metrics/logs ----------------------------|
  \                                                   |
   \-- adapter / OpenLineage -------------------------|---> DATADOG
                                                      |     Jobs Monitoring
DQ externo ---- eventos / métricas -------------------|     Quality Monitoring
                                                      |     Lineage
AWS / Glue ---- native / OpenLineage -----------------|     Monitors / SLO
                                                            Incident Management
                                                                  |
                                                                  v
                                                              ServiceNow
```

## 6.3 Databricks

Datadog possui capacidades específicas para Databricks Data Observability, incluindo Jobs Monitoring e Quality Monitoring.

A integração pode utilizar informações de system tables e outros mecanismos nativos para correlacionar jobs, dados, infraestrutura e custos.

## 6.4 OpenLineage

Datadog aceita eventos OpenLineage para custom jobs.

Isso é relevante porque permite manter adapters e produtores em um padrão aberto para fontes sem integração nativa adequada.

Por exemplo:

```text
ADF
 |
 v
Adapter
 |
 v
OpenLineage
 |
 v
Datadog
```

## 6.5 Data Quality

Datadog pode executar/monitorar controles relacionados a:

- freshness;
- volume;
- nullness;
- uniqueness;
- cardinalidade;
- distribuições;
- anomalias;
- custom SQL e outros sinais suportados pela plataforma.

Regras funcionais complexas não precisam obrigatoriamente ser migradas para o Datadog. Um framework corporativo pode continuar executando regras e publicar resultados na plataforma.

## 6.6 Configuração

Se Datadog for adquirido, a recomendação é **não criar uma camada YAML obrigatória acima dele**.

Recursos como:

- monitores;
- SLOs;
- dashboards;
- alertas;
- incidentes;
- roteamento;

podem ser administrados diretamente na plataforma quando o produto atender aos requisitos.

Terraform/API podem ser utilizados quando existir necessidade concreta de automação e governança como código.

## 6.7 ServiceNow

Este é um dos maiores diferenciais para o contexto atual.

A integração Datadog-ServiceNow permite criar tickets/incidentes a partir de alertas e Incident Management. A integração de Incident Management também suporta sincronização bidirecional de informações como estado, impacto e urgência.

Isso permite uma evolução natural:

```text
MVP
Control-M ---> ServiceNow

Futuro
Datadog Incident <----> ServiceNow
```

sem exigir que a equipe construa toda a integração ITSM.

## 6.8 Prós

- Forte observabilidade operacional.
- Data Observability integrada à visão de infraestrutura e aplicação.
- Boa integração com Databricks.
- Suporte a OpenLineage para custom jobs.
- Capacidade multi-cloud.
- Integração forte com ServiceNow.
- Reduz muito o desenvolvimento de alertas, incidentes, dashboards e SLOs.
- Recursos nativos de anomaly detection.
- Menor tempo para geração de valor.
- Pode substituir várias camadas que seriam desenvolvidas internamente.
- Possibilidade de correlacionar falha de dados com infraestrutura e aplicações.

## 6.9 Contras

- Custo de licença SaaS pode ser significativo.
- Dependência do modelo comercial e de precificação do fornecedor.
- Necessidade de análise de segurança, residência e envio de metadata.
- Parte do modelo corporativo precisará ser mapeada para tags, teams, services ou conceitos Datadog.
- A integração nativa com ADF é mais forte na observabilidade operacional do que no lineage detalhado; um adapter pode continuar necessário.
- Regras de Data Quality altamente específicas podem continuar exigindo execução externa.
- Algumas capacidades de Data Observability/OpenLineage devem ser validadas em POC quanto a maturidade, cobertura e disponibilidade contratual.
- Lock-in maior que em uma solução interna baseada em padrões abertos.

## 6.10 Risco principal

O principal risco é adquirir uma plataforma ampla e cara sem confirmar previamente quais módulos e volumes serão necessários.

A POC precisa ser acompanhada de estimativa comercial realista utilizando volume de jobs, tabelas, monitores, logs e retenção representativos do ambiente corporativo.

## 6.11 Avaliação

É a alternativa que atualmente parece apresentar **melhor equilíbrio entre aderência funcional, velocidade de implantação e redução de esforço interno**, especialmente pela combinação de Databricks, Data Observability, observabilidade operacional e ServiceNow.

---

# 7. DataHub

## 7.1 Descrição

DataHub é uma metadata platform orientada a catálogo, discovery, governance, lineage, Data Quality/Observability e Data Contracts.

Pode ser utilizado como open source ou como serviço gerenciado/DataHub Cloud.

## 7.2 Arquitetura potencial

```text
ADF ---------------- connector ----------------\
Databricks -------- connector ------------------|
OpenLineage ------------------------------------|--> DataHub
DQ / Assertions --------------------------------|    Metadata Graph
Data Contracts ---------------------------------|    Lineage
                                                   Assertions
                                                   Incidents
                                                   Catalog / Governance
```

## 7.3 Aderência ao contexto

DataHub possui conectores para Azure Data Factory e Databricks e consegue receber eventos OpenLineage.

Também possui conceitos de Assertions, Data Contracts, Incidents, ownership, domains e metadata customizada.

Isso cria boa aderência conceitual para uma empresa que deseja unir catálogo, lineage e confiabilidade de dados.

## 7.4 Prós

- Excelente plataforma de metadata e lineage.
- Suporte a OpenLineage.
- Conectores para ADF e Databricks.
- APIs e SDKs extensíveis.
- Assertions e recursos de Data Quality/Observability.
- Incidents e Data Contracts fazem parte do modelo da plataforma.
- Boa aderência a ownership, domains e metadata corporativa.
- Opção open source reduz dependência exclusiva de SaaS.
- DataHub Cloud reduz custo operacional do self-hosted.

## 7.5 Contras

- O foco principal é metadata/catalog/governance, e não operação de pipelines no mesmo nível de uma plataforma como Datadog.
- Pode sobrepor funções já existentes no Unity Catalog e em uma eventual estratégia Purview.
- O modelo de incidentes precisa ser validado contra o processo corporativo de ServiceNow.
- Self-hosting aumenta significativamente a responsabilidade operacional.
- Se adquirido apenas para observabilidade, parte relevante do produto pode não ser utilizada.
- Pode induzir a discussão a migrar de Observabilidade para uma nova plataforma corporativa de catálogo, aumentando o escopo.

## 7.6 Risco principal

O principal risco é transformar uma iniciativa de Observabilidade e DQ em uma nova iniciativa de metadata platform/catalog, sobrepondo funções de governança existentes.

## 7.7 Avaliação

É uma ótima candidata quando **catálogo, metadata graph e governança** forem parte importante da decisão.

Para o problema atual, parece menos alinhada que Datadog com a necessidade de observabilidade operacional e ITSM, mas mais alinhada caso a organização queira consolidar também uma plataforma de metadata corporativa.

---

# 8. OpenMetadata

## 8.1 Descrição

OpenMetadata é uma plataforma de metadata que combina discovery, governance, lineage, Data Quality, profiling, observability, alerts e incident management.

Possui forte orientação open source e pode ser operada internamente ou por oferta gerenciada do ecossistema.

## 8.2 Arquitetura potencial

```text
Databricks / Unity Catalog ---- connector ----\
Databases --------------------- connector -----|
OpenLineage ------------------- Kafka/Kinesis -|--> OpenMetadata
DQ / Profiler ---------------------------------|    Catalog
                                                    Lineage
                                                    Quality
                                                    Alerts
                                                    Incidents
```

## 8.3 Databricks

OpenMetadata possui conectores para Databricks/Unity Catalog com suporte a metadata, profiling, lineage e Data Quality.

## 8.4 OpenLineage

OpenMetadata possui conector que consome eventos OpenLineage e os traduz para informações de lineage.

Isso permite utilizar OpenLineage como padrão de interoperabilidade para fontes suportadas.

## 8.5 Prós

- Forte conjunto integrado de catálogo, lineage e DQ.
- Data Quality nativo.
- Profiler e observability integrados.
- Alertas e incident management.
- Suporte a OpenLineage.
- Boa extensibilidade.
- Reduz dependência de fornecedor em comparação a uma solução SaaS fechada quando self-hosted.
- Pode executar testes e apresentar resultados na mesma plataforma.

## 8.6 Contras

- Self-hosting representa uma nova plataforma crítica para sustentar.
- Exige operação de componentes, ingestão e atualização da própria ferramenta.
- Pode sobrepor Unity Catalog/Purview na camada de catálogo e governança.
- A cobertura ADF precisa ser validada em profundidade para o cenário específico.
- Integração com processo corporativo de ServiceNow precisa ser validada/construída.
- Não possui o mesmo foco de observabilidade operacional integrada a infraestrutura que Datadog.
- Pode exigir uma curva relevante de aprendizado e administração.

## 8.7 Risco principal

Assim como DataHub, o risco é introduzir uma metadata platform inteira para resolver um problema cujo foco principal é observabilidade operacional + confiabilidade + suporte.

## 8.8 Avaliação

É uma alternativa tecnicamente válida e especialmente interessante se a estratégia corporativa privilegiar open source e uma plataforma integrada de metadata + DQ.

No contexto atual, não seria a primeira opção para POC.

---

# 9. Monte Carlo

## 9.1 Descrição

Monte Carlo é uma plataforma SaaS especializada em Data Observability.

Seu foco está diretamente na confiabilidade dos dados: freshness, volume, anomalias, lineage, incidentes de dados e correlação com pipelines e warehouses.

## 9.2 Arquitetura potencial

```text
Azure Data Factory ---- native integration ----\
Databricks ------------ native integration -----|--> Monte Carlo
Data Warehouse / BI ---- integrations ----------|    Data Observability
                                                  |    Lineage
                                                  |    Monitors
                                                  |    Incidents
                                                  |    Routing
```

## 9.3 Azure Data Factory

Monte Carlo possui integração específica com ADF.

A integração atual oferece, entre outras capacidades:

- pipeline failure alerts;
- histórico de pipeline runs;
- table-to-table lineage;
- correlação entre pipeline e anomalias de dados;
- centralização de triagem e notificações de problemas de qualidade e pipelines.

Essa cobertura nativa do ADF é um diferencial relevante.

## 9.4 Databricks

Monte Carlo possui integração com Databricks e Databricks Workflows.

Pode consumir system tables para lineage, queries e informações de jobs/tasks, além de correlacionar workflow failures com incidentes de dados.

## 9.5 Prós

- Produto diretamente especializado em Data Observability.
- Boa integração com ADF.
- Boa integração com Databricks/Unity Catalog.
- Lineage e anomalias integrados.
- Menor esforço de desenvolvimento.
- Rápido time-to-value.
- Boa experiência centrada em incidentes de dados.
- Reduz necessidade de construir detecção estatística e correlação.

## 9.6 Contras

- Plataforma SaaS especializada, com custo e lock-in.
- Menor foco em observabilidade geral de infraestrutura e aplicações que Datadog.
- Integração com o modelo corporativo de `support_team` precisa ser validada.
- Integração e sincronização com ServiceNow devem ser avaliadas especificamente no processo corporativo.
- Data Contracts e regras corporativas podem exigir integrações adicionais.
- Parte da observabilidade técnica continuará em outras plataformas, podendo manter múltiplas telas para suporte.

## 9.7 Risco principal

O risco é criar uma solução excelente para incidentes de dados, mas manter separada a observabilidade técnica de infraestrutura/aplicações e o fluxo ITSM corporativo.

## 9.8 Avaliação

É uma das alternativas SaaS mais aderentes ao problema de **Data Observability puro**.

Deve ser considerada se o foco prioritário for confiabilidade de dados e se a organização preferir uma ferramenta especializada em vez de uma plataforma mais ampla como Datadog.

---

# 10. Microsoft Purview

## 10.1 Descrição

Microsoft Purview Unified Catalog possui capacidades de catálogo, governance, lineage e Data Quality.

No contexto Azure, apresenta integração natural com serviços Microsoft e suporte ao Azure Databricks Unity Catalog.

## 10.2 Databricks

Purview consegue escanear Unity Catalog e obter metadata de metastores, catalogs, schemas, tabelas, views, colunas e tags.

Também utiliza informações de lineage disponíveis no Databricks.

O Purview Unified Catalog possui suporte de Data Quality para Azure Databricks Unity Catalog.

## 10.3 Limitações relevantes

Há limitações importantes para tratá-lo como solução central de Observabilidade.

Por exemplo, lineage extraído de Databricks não necessariamente representa dependências entre um serviço externo que dispara um notebook/job e os ativos Databricks.

Isso é particularmente relevante em fluxos como:

```text
ADF --> Databricks Job --> Tabela
```

A dependência operacional ADF -> Databricks pode não ser representada automaticamente apenas pela integração de lineage do Purview.

## 10.4 Prós

- Forte alinhamento com ambiente Microsoft.
- Boa integração com Unity Catalog.
- Catálogo e governança corporativa.
- Data Quality disponível para fontes suportadas, incluindo Azure Databricks Unity Catalog.
- Pode aproveitar contratos e relacionamento já existentes com Microsoft.
- Baixa barreira para integração com recursos Azure.

## 10.5 Contras

- Não é uma plataforma de observabilidade operacional de pipelines comparável a Datadog.
- Incident management e support routing não são o centro da proposta do produto.
- O fluxo Control-M/ServiceNow continuaria separado.
- Lineage cross-platform possui limitações.
- Menor portabilidade para uma estratégia multi-cloud independente de Microsoft.
- Não elimina a necessidade de uma camada operacional de Observabilidade.

## 10.6 Avaliação

Purview deve ser considerado principalmente como **componente complementar de governança, catálogo, lineage e Data Quality**, e não como backbone principal desta iniciativa.

---

# 11. Comparação qualitativa

Legenda:

- **Muito Alta** — atende de forma natural e madura ao requisito;
- **Alta** — boa aderência, com pequenas adaptações;
- **Média** — atende parcialmente ou exige trabalho relevante;
- **Baixa** — não é foco natural da solução.

| Critério | Desenvolvimento interno | Datadog | DataHub | OpenMetadata | Monte Carlo | Purview |
|---|---|---|---|---|---|---|
| Observabilidade operacional | Alta, após desenvolvimento da camada operacional descrita no Cen2 | Muito Alta | Média | Média | Alta para dados | Baixa |
| Databricks | Muito Alta | Muito Alta | Alta | Alta | Muito Alta | Alta |
| ADF | Muito Alta (Diagnostic Settings + Event Hub, sem adapter) | Média/Alta | Alta | Média a validar | Muito Alta | Alta |
| Data Quality | Alta com framework (API própria de ingestão de resultados) | Alta | Alta | Muito Alta | Muito Alta | Alta em fontes suportadas |
| SLO | Alta, com construção | Muito Alta | Média/Alta | Média/Alta | Alta | Média |
| Lineage L1/L2 | Rastreabilidade L1 forte (processo → execução → ativo); L2 dependente da fonte nativa (Unity Catalog), com lacuna conhecida em lineage cross-technology (ADF → Databricks → Power BI) | Alta | Muito Alta | Muito Alta | Alta | Alta com limitações cross-tool |
| Support routing | Muito Alta, com construção (modelo de herança e overrides já desenhado no Cen2) | Alta | Média/Alta | Média/Alta | Alta a validar | Baixa |
| ServiceNow | Média via processo atual (Control-M); Alta em evolução futura com integração direta | Muito Alta | Média a validar | Média a validar | Média/Alta a validar | Média |
| Multi-cloud | Alta no modelo conceitual (identidade e eventos independentes de tecnologia); implementação atual roda em Azure | Muito Alta | Muito Alta | Muito Alta | Muito Alta | Média |
| Data Contracts | Muito Alta (modelo desenhado para consumir referências e violações diretamente) | Alta via integração | Muito Alta | Alta | Alta via integração | Média/Alta |
| Time-to-value | Média (arquitetura detalhada, mas exige construir e sustentar cada capacidade) | Muito Alta | Alta em Cloud | Média | Muito Alta | Alta para governança |
| Esforço interno | Alto (backend, adapters, Metadata Collector, avaliador temporal, correlação e todas as integrações) | Baixo/Médio | Médio em Cloud / Alto self-hosted | Alto self-hosted | Baixo/Médio | Baixo/Médio |
| Controle do modelo | Muito Alto | Médio | Alto | Alto | Médio | Médio |
| Lock-in | Baixo/Médio (modelo canônico portável; implementação atual em Azure) | Alto | Médio | Baixo/Médio | Alto | Alto Microsoft |

---

# 12. Comparação de custo: cuidado com infraestrutura versus TCO

Uma comparação apenas de custo de infraestrutura pode levar a uma conclusão incorreta.

Por exemplo, a alternativa de Desenvolvimento interno pode utilizar componentes relativamente baratos:

- Event Hub;
- ADLS;
- Delta;
- Container Apps;
- PostgreSQL;
- Power BI, já disponível corporativamente (Power BI Premium). Grafana foi avaliado apenas como comparação de custo em uma fase anterior do desenho e não faz parte da arquitetura-base (ver Cen2, seção "Visão de consumo" / Power BI).

Entretanto, seu custo total também inclui:

```text
engenharia de adapters
+ desenvolvimento da camada operacional
+ dashboards
+ routing
+ SLO engine
+ alert engine
+ incident integration
+ upgrades
+ segurança
+ testes
+ plantão
+ correções de schemas
+ documentação
+ suporte
```

Para uma solução SaaS, a licença pode ser significativamente maior que a infraestrutura bruta, mas substitui parte desse trabalho.

Portanto, a análise deve considerar **TCO — Total Cost of Ownership**, e não apenas a fatura mensal de cloud.

---

# 13. Construir versus comprar

A decisão central pode ser resumida da seguinte forma.

## Construir

Escolher **Desenvolvimento interno** se a organização considerar estratégicos:

- controle total do modelo;
- portabilidade;
- uso de padrões abertos;
- redução de dependência de fornecedor;
- customização profunda;
- capacidade interna para construir e sustentar a plataforma.

## Comprar

Escolher uma plataforma SaaS se a prioridade for:

- time-to-value;
- redução de engenharia interna;
- UX pronta;
- anomaly detection pronta;
- incident workflows;
- integrações mantidas pelo fornecedor;
- menor responsabilidade operacional da plataforma.

Ao comprar, a recomendação é utilizar realmente o produto. Não faz sentido adquirir uma plataforma SaaS e reconstruir externamente toda sua camada de configuração, monitoramento, SLO, alertas e incidentes apenas para criar abstração de fornecedor.

---

# 14. Recomendação

## 14.1 Melhor alternativa geral: Datadog

Considerando o escopo atual, a Solução Conceitual e o contexto operacional, **Datadog é a alternativa que eu avaliaria primeiro e que atualmente considero a melhor candidata geral**, condicionado a uma POC técnica e comercial satisfatória.

Os principais motivos são:

1. combina observabilidade técnica e Data Observability;
2. possui integração forte com Databricks;
3. suporta OpenLineage para fontes/custom jobs;
4. oferece Quality Monitoring e lineage;
5. já possui mecanismos maduros de monitores, SLOs, dashboards e incidentes;
6. possui integração particularmente forte com ServiceNow, inclusive sincronização bidirecional no Incident Management;
7. reduz bastante o volume de componentes que precisaríamos desenvolver e sustentar;
8. é multi-cloud;
9. não impede manter identidade, ownership, Data Contracts e outros conceitos corporativos fora da ferramenta quando necessário.

O principal ponto a validar é a combinação de **custo + cobertura real do ADF + modelo de licenciamento + requisitos corporativos de segurança e residência de metadata**.

## 14.2 Segunda alternativa: Desenvolvimento interno

Se a contratação de Datadog não for economicamente adequada ou se a empresa decidir que a capacidade é estratégica demais para depender de um SaaS, a melhor alternativa é **Desenvolvimento interno**, na arquitetura consolidada descrita no Cen2.

Essa solução deve ser a referência técnica para comparar o valor real de um SaaS. Ela combina aderência ao ambiente Azure/Databricks atual — aproveitando componentes de plataforma já disponíveis, como Unity Catalog, Event Hub e Power BI Premium, para reduzir a barreira de entrada — com um modelo canônico independente de tecnologia e maior portabilidade para expansão futura a outras clouds e plataformas. Escolher essa alternativa exige aceitar explicitamente que a organização estará desenvolvendo e sustentando um produto interno, com todo o esforço de engenharia, operação e suporte que isso implica — não apenas a infraestrutura de baixo custo.

## 14.3 Terceira alternativa: Monte Carlo

Monte Carlo merece uma avaliação caso a prioridade seja **Data Observability especializada**.

Sua integração nativa com ADF e Databricks é particularmente interessante.

Eu colocaria Monte Carlo abaixo de Datadog para o cenário atual porque Datadog aproxima mais:

- operação técnica;
- dados;
- incident management;
- ServiceNow;
- multi-cloud.

Entretanto, para uma organização que deseje uma ferramenta focada especificamente em confiabilidade dos dados, Monte Carlo pode superar Datadog em determinados casos de uso.

## 14.4 DataHub

DataHub passa a ser uma opção prioritária se a organização decidir que precisa, além de Observabilidade, de uma **metadata platform corporativa** para catálogo, discovery, lineage, Data Contracts e governance.

Nesse caso, a comparação deve ser feita em um escopo maior que esta iniciativa.

## 14.5 OpenMetadata

OpenMetadata é uma alternativa válida principalmente quando houver preferência estratégica por open source e intenção de consolidar catálogo, lineage e DQ em uma única plataforma administrada pela própria organização.

Não seria minha primeira POC para o problema atual.

## 14.6 Purview

Purview deve ser avaliado como componente complementar e possivelmente como parte da estratégia corporativa de governance/catalog.

Não o escolheria como plataforma central de Observabilidade operacional.

---

# 15. Ranking atual

Considerando o problema específico de Observabilidade e Data Quality definido nesta iniciativa, meu ranking atual é:

| Posição | Alternativa | Papel recomendado |
|---|---|---|
| **1** | **Datadog Data Observability** | Primeira opção para POC e potencial adoção |
| **2** | **Desenvolvimento interno** | Melhor alternativa interna e benchmark de TCO |
| **3** | **Monte Carlo** | SaaS especializado a considerar se Data Observability for prioridade absoluta |
| **4** | **DataHub** | Forte se metadata platform/catalog também entrar no escopo estratégico |
| **5** | **OpenMetadata** | Alternativa open source integrada de metadata + DQ |
| — | **Microsoft Purview** | Complementar; não recomendado como backbone operacional principal |

Esse ranking não deve ser interpretado como decisão de contratação antes de POC e estimativa comercial.

---

# 16. POC recomendada

A POC deve comparar inicialmente apenas as duas alternativas mais relevantes:

```text
Datadog
   versus
Desenvolvimento interno
```

Monte Carlo pode ser incluído como terceira alternativa se houver interesse comercial em uma plataforma especializada.

## 16.1 Casos obrigatórios da POC

### Caso 1 — Falha de pipeline ADF crítico

Validar:

- detecção da falha;
- identificação do pipeline/run;
- criticidade;
- `support_team`;
- notificação;
- correlação com ServiceNow/Control-M conforme arquitetura.

### Caso 2 — Databricks Workflow

Validar:

- job;
- task;
- duração;
- falha;
- histórico;
- custo/contexto quando disponível;
- correlação com ativo produzido.

### Caso 3 — Data Quality simples

Exemplos:

- null rate;
- uniqueness;
- freshness;
- volume.

### Caso 4 — Data Quality funcional complexa

Executar uma regra externa e validar se a solução consegue:

- receber o resultado;
- correlacionar ao ativo;
- preservar a regra;
- gerar condição;
- rotear para equipe correta.

### Caso 5 — SLO

Exemplo:

```text
finance.orders
available_by = 06:00
```

Validar definição, medição, violação e recuperação.

### Caso 6 — Lineage

Validar pelo menos:

```text
ADF
 |
 v
Databricks
 |
 v
Delta table A
 |
 v
Delta table B
```

O objetivo é comprovar Lineage Nível 1 e Nível 2.

### Caso 7 — Roteamento por equipe

Utilizar o mesmo ativo com condições diferentes:

```text
falha de pipeline
    -> Data Platform VCBR

falha de DQ
    -> Data Quality Comercial

SLO global
    -> Global Data Operations
```

### Caso 8 — ServiceNow

Para Datadog, testar:

- criação automática;
- assignment group;
- número do incidente;
- atualização;
- resolução;
- sincronização de estado.

Para a alternativa interna, estimar explicitamente o desenvolvimento necessário para atingir a mesma capacidade.

### Caso 9 — Hub/Spoke

Utilizar ativos em dois metastores e comprovar que a solução diferencia corretamente objetos com o mesmo nome físico.

### Caso 10 — Data Contract

Simular um Data Contract que defina uma expectativa de freshness e validar a criação ou atualização da configuração operacional correspondente.

---

# 17. Critério de decisão após POC

Ao final da POC, a decisão não deve ser baseada apenas na quantidade de funcionalidades demonstradas.

Deve considerar:

```text
aderência funcional
+ experiência operacional
+ segurança
+ esforço de integração
+ esforço de sustentação
+ licenciamento
+ infraestrutura
+ engenharia necessária
+ suporte do fornecedor
+ portabilidade necessária
= TCO e risco real
```

A principal pergunta é:

> O custo adicional de uma plataforma SaaS é menor ou maior que o custo e o risco de construir e sustentar internamente as capacidades equivalentes?

Essa comparação dará uma base mais confiável para a decisão final do que uma análise isolada de preço de infraestrutura ou quantidade de features.

---

# 18. Conclusão

Há dois caminhos tecnicamente sólidos.

O primeiro é **comprar uma plataforma e utilizar suas capacidades nativas de forma efetiva**. Nesse caminho, Datadog é atualmente a opção que apresenta melhor aderência global ao problema definido, principalmente por combinar observabilidade operacional, Databricks, Data Observability, OpenLineage e integração com ServiceNow.

O segundo é **construir a arquitetura de Desenvolvimento interno**, aproveitando os componentes de plataforma já disponíveis no ambiente Azure/Databricks atual (Unity Catalog, Event Hub, Power BI Premium) e mantendo um modelo canônico independente de tecnologia, com OpenLineage disponível como mecanismo de interoperabilidade quando houver benefício concreto — e não como substituto do modelo conceitual corporativo.

Não recomendo, neste momento, decidir por Marquez como núcleo obrigatório da arquitetura nem construir uma camada externa de YAML/configuração apenas para reproduzir recursos que uma solução SaaS já oferece.

A próxima decisão deve, portanto, comparar concretamente **Datadog versus Desenvolvimento interno**, usando a POC e o TCO como critérios principais.
