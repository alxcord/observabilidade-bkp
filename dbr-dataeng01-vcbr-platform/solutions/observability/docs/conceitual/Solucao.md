# Solução conceitual

## 1. Objetivo

Este documento define a **Solução Conceitual** para a capacidade corporativa de Observabilidade e Data Quality.

O documento complementa o arquivo **Observabilidade e Data Quality - Escopo.md**. Enquanto o Escopo define **o que** a capacidade deve atender, esta Solução Conceitual estabelece **como os conceitos devem ser organizados e relacionados**, sem selecionar uma arquitetura física ou produto específico.

A solução deve permanecer independente da tecnologia de implementação. O ambiente atual é predominantemente Azure e Databricks, mas o modelo deve suportar evolução para outras regiões, plataformas, clouds e mecanismos de processamento.

As arquiteturas candidatas, incluindo Cen1 e Cen2, devem ser avaliadas posteriormente quanto à capacidade de implementar este modelo conceitual.

---

## 2. Documentos de referência e fronteiras

Esta solução conceitual considera como referências principais:

- o Escopo de Observabilidade e Data Quality;
- a Proposta de Arquitetura Global de Dados para fontes compartilhadas;
- as políticas corporativas de governança e tags;
- a frente específica responsável por Data Contracts.

### 2.1 O que pertence à Solução Conceitual

Fazem parte deste documento:

- modelo canônico de entidades;
- identidade lógica de ativos;
- estrutura conceitual de eventos;
- estrutura de regras e resultados de Data Quality;
- configuração de SLI/SLO/SLA para observabilidade;
- uso de metadados de governança;
- integração conceitual com Data Contracts;
- rastreabilidade operacional entre processos, execuções e ativos;
- consumo opcional de informações de lineage provenientes de plataformas nativas ou fontes externas;
- regras conceituais de correlação e deduplicação;
- modelo de roteamento de suporte;
- integração conceitual com ITSM;
- abordagem de configuração operacional auditável, versionável e automatizável;
- separação entre eventos históricos, condições ativas e sistemas externos.

### 2.2 O que não pertence à Solução Conceitual

Não fazem parte deste documento:

- seleção de produto ou ferramenta;
- desenho físico de infraestrutura;
- escolha entre Cen1 e Cen2;
- configuração detalhada de Azure, Databricks, ServiceNow ou Control-M;
- política corporativa definitiva de tags;
- definição dos Data Contracts;
- mecanismo responsável por detectar mudanças ou violações de Data Contracts;
- metodologia definitiva de Quality Score;
- manutenção obrigatória de um repositório corporativo próprio de lineage;
- lineage upstream/downstream como requisito obrigatório de implementação ou aceite;
- análise de impacto e column-level lineage como requisitos obrigatórios;
- FinOps.

---

## 3. Princípios da solução conceitual

A solução deve seguir os seguintes princípios.

### 3.1 Modelo comum, fontes heterogêneas

ADF, Databricks, ferramentas de Data Quality, mecanismos de compartilhamento, futuros serviços AWS e outras tecnologias devem ser traduzidos para um modelo canônico comum.

A tecnologia de origem não deve determinar o modelo corporativo.

### 3.2 Eventos históricos são fatos imutáveis

Uma execução, falha, resultado de qualidade ou violação recebida deve ser preservada como fato histórico.

Um evento histórico não deve ser alterado para representar mudanças posteriores de estado.

### 3.3 Configuração é separada de execução

Definições como regras de qualidade, SLOs e regras de alertamento devem existir separadamente dos resultados produzidos por sua execução.

### 3.4 Governança é fonte de contexto

Ownership, domínio, unidade operacional, criticidade, escopo do dado e demais classificações devem ser obtidos de fontes corporativas de governança quando disponíveis.

A ausência de metadados de governança não impede que o ativo seja observado. A ausência deve ser registrada e eventualmente tratada como não conformidade.

### 3.5 Data Contracts são uma fonte externa

A Observabilidade pode utilizar referências e violações de Data Contracts como contexto, mas não é responsável por definir, versionar, aprovar ou detectar primariamente violações de contrato.

### 3.6 Responsabilidade pelo ativo e responsabilidade pelo incidente são conceitos distintos

O Data Owner, Data Steward, domínio custodiante e equipe de suporte possuem finalidades diferentes.

Uma falha técnica pode ser tratada por uma equipe de plataforma, enquanto uma falha funcional de qualidade no mesmo ativo pode ser tratada por outra equipe.

### 3.7 MVP e estado-alvo devem compartilhar o mesmo modelo

A implementação inicial pode utilizar Control-M e ServiceNow sem integração bidirecional. A evolução futura pode utilizar integração direta com ITSM.

O modelo conceitual deve suportar ambos sem redesenho estrutural.

---

## 4. Visão geral da solução conceitual

A capacidade é organizada em cinco grupos lógicos.

```text
FONTES E METADADOS
    Pipelines / Jobs / Datasets / Governança / Data Contracts
                    |
                    v
DEFINIÇÕES VERSIONADAS
    Assets / DQ Rules / SLOs / Alert Rules / Routing Rules
                    |
                    v
EVENTOS IMUTÁVEIS
    Execuções / DQ Results / Data Observability / Contract Violations
                    |
                    v
CORRELAÇÃO E ESTADO OPERACIONAL
    Rastreabilidade execução↔ativo / Alert Conditions / Support Routing / Deduplication
                    |
                    v
CONSUMO E AÇÃO
    Dashboards / Métricas / Control-M / ITSM / Outros consumidores
```

A implementação física pode combinar tabelas, logs, APIs, filas, arquivos, mecanismos de streaming ou ferramentas especializadas. O requisito é preservar as relações conceituais acima.

---

## 5. Modelo canônico de entidades

O modelo conceitual deve representar, no mínimo, as seguintes entidades.

### 5.1 `process`

Representa um processo executável.

Exemplos:

- pipeline ADF;
- Databricks Job;
- Workflow;
- pipeline de ingestão;
- processo Control-M observado;
- futuro workflow AWS.

Atributos conceituais:

```text
process_id
process_type
process_name
platform
source_native_id
support_team_id
metadata
```

### 5.2 `process_run`

Representa uma execução de processo.

```text
process_run_id
process_id
source_run_id
status
started_at
finished_at
duration
trigger_type
parent_run_id
correlation_id
error_code
error_message
```

### 5.3 `task`

Representa uma atividade ou tarefa interna de um processo.

### 5.4 `task_run`

Representa uma execução de tarefa.

Deve ser correlacionável ao `process_run` pai.

### 5.5 `data_asset`

Representa uma instância técnica observável de um ativo de dados.

Exemplos:

- tabela;
- view;
- dataset;
- stream;
- tópico;
- arquivo lógico governado;
- produto de dados.

A identidade do ativo é detalhada na seção 6.

### 5.6 `quality_rule`

Representa a definição de uma regra de qualidade.

### 5.7 `quality_result`

Representa o resultado de uma execução de regra de qualidade.

### 5.8 `slo_definition`

Representa um objetivo operacional ou de confiabilidade observável.

### 5.9 `observability_event`

Representa um fato normalizado ocorrido na plataforma.

### 5.10 `alert_condition`

Representa uma condição operacional atualmente observada, derivada de um ou mais eventos.

Não representa necessariamente um chamado ITSM.

### 5.11 `support_team`

Representa a equipe lógica responsável pelo tratamento operacional de determinado tipo de falha.

### 5.12 `itsm_reference`

Representa, quando disponível, o vínculo entre uma condição operacional e um chamado em um sistema ITSM.

### 5.13 `contract_reference`

Representa os metadados mínimos de Data Contract associados a um ativo ou evento.

---

## 6. Identidade lógica de ativos

### 6.1 Problema

A identidade de um ativo não pode depender apenas de seu nome físico.

Dois objetos podem possuir o mesmo `catalog.schema.table` em regiões ou metastores diferentes e serem ativos distintos. Da mesma forma, um único ativo pode ser acessado por vários workspaces sem se tornar vários ativos.

No modelo Hub/Spoke, a identidade também precisa distinguir:

- ativos locais;
- aquisições compartilhadas;
- produtos do Hub;
- objetos recebidos por compartilhamento;
- representações técnicas distintas de um mesmo produto autoritativo.

### 6.2 Identificador canônico

A identidade técnica deve ser baseada conceitualmente em:

```text
asset_instance_key =
    platform
  + platform_account
  + namespace_authority
  + namespace
  + object_type
  + object_name
```

O sistema pode gerar um `asset_id` estável a partir dessa chave, por UUID determinístico, hash ou mecanismo equivalente.

Também é recomendável manter uma representação legível, denominada neste documento `asset_urn`.

### 6.3 Significado dos componentes

#### `platform`

Tecnologia que governa o namespace do objeto.

Exemplos:

```text
databricks
aws_glue
snowflake
bigquery
kafka
```

#### `platform_account`

Fronteira administrativa superior da plataforma quando necessária para evitar colisão.

No Databricks pode representar a conta Databricks ou outro identificador administrativo estável.

#### `namespace_authority`

Autoridade responsável por garantir unicidade dentro de determinado namespace.

Para Unity Catalog, o exemplo natural é o **Metastore ID**.

Para outras tecnologias pode ser:

- Glue Catalog + região;
- Snowflake Account;
- outro identificador equivalente.

#### `namespace`

Caminho lógico do objeto dentro da autoridade.

No Databricks:

```text
catalog.schema
```

#### `object_type`

Exemplos:

```text
table
view
stream
topic
file_dataset
data_product
```

#### `object_name`

Nome do objeto dentro do namespace.

### 6.4 Campos de localização que não devem compor obrigatoriamente a identidade

Os campos abaixo são importantes para localização, troubleshooting e governança, mas não devem ser considerados automaticamente parte da chave lógica:

```text
cloud_provider
cloud_subscription_id
cloud_account_id
cloud_region
resource_group
workspace_id
workspace_name
storage_account
```

O motivo é que um ativo pode ser acessado por múltiplos workspaces ou ter sua infraestrutura reorganizada sem necessariamente mudar sua identidade lógica.

---

## 7. Exemplos de identidade no modelo Hub/Spoke

### 7.1 VCBR e Itacamba utilizando o mesmo ativo compartilhado

Considere uma tabela autoritativa de uma origem SAP compartilhada dentro do metastore brasileiro.

```text
platform             = databricks
cloud_provider       = azure
platform_account     = vc-databricks-account
namespace_authority  = metastore-br
namespace            = prd_logistics.corp_cleansed
object_type          = table
object_name          = sap_delivery
```

Metadados de governança podem indicar:

```text
data_scope                 = shared_source
operating_unit             = multi_unit
applicable_operating_units = vcbr,itacamba,vcna
vc_source_system           = ds
source_instance            = sap_shared
object_role                = authoritative
```

Existe **um único `asset_id`** para essa tabela.

VCBR e Itacamba consumirem o mesmo objeto não cria dois ativos distintos.

### 7.2 Produto local da Itacamba no mesmo metastore

```text
platform             = databricks
namespace_authority  = metastore-br
namespace            = prd_industrial.bus_curated
object_type          = table
object_name          = producao_itacamba
```

Metadados:

```text
operating_unit  = itacamba
data_scope      = local
object_role     = local_derivation
custodian_domain = industrial
```

Esse objeto possui outro `asset_id`, embora compartilhe o mesmo metastore com ativos VCBR e ativos compartilhados.

Quando houver uma fonte de lineage disponível, ela pode complementar a investigação mostrando relações como:

```text
SAP compartilhado
      |
      v
objeto autoritativo compartilhado
      |
      v
produto local Itacamba
```

A plataforma de Observabilidade não precisa persistir ou reconstruir essa cadeia para manter a identidade e a rastreabilidade operacional dos ativos.

### 7.3 VCNA em outro metastore

Considere:

```text
metastore-br
prd_logistics.corp_curated.delivery
```

E:

```text
metastore-na
prd_logistics.corp_curated.delivery
```

Mesmo que catálogo, schema e tabela possuam o mesmo nome, os objetos são diferentes porque `namespace_authority` é diferente.

Consequentemente, possuem `asset_id` distintos.

### 7.4 Produto VCEAA publicado ao Hub

Considere um produto certificado produzido pela VCEAA e compartilhado para o Hub.

```text
VCEAA metastore
    asset_id = A
    object_role = authoritative
          |
          | sharing
          v
Hub
    asset_id = B
    object_role = reference/shared_exposure
    authoritative_asset_id = A
```

Existem duas instâncias técnicas observáveis e, portanto, dois `asset_id`.

A relação semântica entre elas deve ser preservada por `authoritative_asset_id` ou mecanismo conceitualmente equivalente.

O Data Contract pode ser o mesmo nas duas pontas.

### 7.5 Databricks em AWS

A mudança de cloud não altera o modelo:

```text
platform             = databricks
cloud_provider       = aws
platform_account     = <databricks-account>
namespace_authority  = <unity-catalog-metastore-id>
namespace            = catalog.schema
object_type          = table
object_name          = orders
```

### 7.6 AWS sem Databricks

Para um catálogo AWS Glue:

```text
platform             = aws_glue
cloud_provider       = aws
platform_account     = <aws-account-id>
namespace_authority  = <aws-account-id + region + glue-catalog>
namespace            = <database>
object_type          = table
object_name          = orders
```

O modelo corporativo não precisa mudar. Apenas o adapter responsável por traduzir os identificadores nativos para o modelo canônico é diferente.

---

## 8. Metadados de governança e enriquecimento

Os eventos e entidades observáveis devem ser enriquecidos, quando disponível, com metadados provenientes das políticas corporativas.

A lista abaixo é indicativa e deve acompanhar a política de governança vigente:

```text
vc_data_domain
vc_source_system
source_instance
operating_unit
applicable_operating_units
data_scope
object_role
custodian_domain
authoritative_object / authoritative_asset_id
vc_data_owner
vc_data_steward
vc_criticality
lifecycle_status
sharing_mode
sharing_audience
data_residency
```

A Solução Conceitual **não determina quais dessas tags são obrigatórias**. Essa decisão pertence à política de governança.

### 8.1 Ausência de metadados

Um ativo sem metadados obrigatórios não deve ser excluído da observabilidade.

Exemplo:

```text
asset_id = X
vc_data_owner = null
```

A plataforma deve continuar observando o ativo e pode produzir um indicador de não conformidade de governança.

---

## 9. Gestão de configurações operacionais

### 9.1 Princípio

Definições operacionais devem ser **estruturadas, auditáveis, versionáveis e automatizáveis**, além de permanecer separadas dos dados de execução.

A Solução Conceitual **não determina uma tecnologia única como fonte autoritativa das configurações operacionais**. A fonte de verdade pode ser:

- a própria plataforma de observabilidade, quando ela oferecer governança, histórico de alteração, API e mecanismos adequados de controle;
- um repositório externo declarativo, como Git com YAML, JSON, Terraform ou formato equivalente;
- uma plataforma corporativa responsável pela definição, como Data Contracts ou governança, com sincronização para a solução de observabilidade;
- uma combinação desses modelos, desde que a responsabilidade e a proveniência de cada definição sejam explícitas.

O requisito conceitual é preservar **controle, rastreabilidade, automação e proveniência**, e não obrigar uma forma específica de armazenamento.

### 9.2 Princípio de adoção de plataforma de mercado

Quando a implementação utilizar uma plataforma SaaS ou produto especializado, a solução deve aproveitar suas capacidades nativas quando elas atenderem adequadamente aos requisitos corporativos.

Não é objetivo desta iniciativa construir uma camada paralela apenas para abstrair recursos que a ferramenta já fornece de forma satisfatória.

Exemplos de capacidades que podem ser administradas diretamente na plataforma adotada:

```text
monitores
SLOs
regras nativas de Data Observability
alertas
roteamento operacional
dashboards
incident workflows
anomalias
```

Um repositório externo como Git/YAML continua válido quando existir necessidade concreta de:

- configuração como código;
- revisão e aprovação por pull request;
- automação em escala;
- geração a partir de outra fonte;
- portabilidade entre ferramentas;
- controles de segregação de função não atendidos pela plataforma.

### 9.3 Fronteira de portabilidade

A independência tecnológica deve ser preservada principalmente nos conceitos corporativos que precisam sobreviver à troca de ferramenta, como:

```text
asset identity
ownership
support_team
Data Contract references
correlation identifiers
rastreabilidade entre execução e ativo
referências de interoperabilidade com fontes de lineage, quando aplicável
resultados históricos relevantes
```

Recursos operacionais específicos de uma plataforma podem permanecer nativos quando isso reduzir complexidade e esforço operacional sem comprometer requisitos corporativos relevantes.

### 9.4 Categorias de configuração

A solução deve ser capaz de representar, independentemente de onde sejam administradas:

```text
assets
quality rules
SLOs
alert rules
support routing
```

### 9.5 Representação declarativa opcional

Quando for adotado um modelo externo declarativo, YAML pode ser utilizado como uma das representações possíveis.

Exemplo:

```yaml
source:
  type: data_contract
  reference: global.finance.orders
  version: "3"
```

Esse exemplo é ilustrativo. O mesmo conceito pode ser representado por API, banco de configuração, Terraform, JSON ou recurso nativo da plataforma.

### 9.6 Proveniência da definição

Configurações criadas ou sincronizadas a partir de outra fonte devem preservar sua origem e, quando aplicável, sua versão.

Isso permite auditoria, reconciliação e atualização automática quando a fonte original mudar.

---

## 10. Modelo canônico de eventos

### 10.1 Envelope comum

Todo evento deve possuir um envelope comum e uma carga específica por tipo.

Exemplo conceitual:

```text
event_id
event_type
event_version
occurred_at
observed_at
source_platform
vc_source_system
source_native_id
correlation_id
process_id
process_run_id
task_run_id
asset_id
severity
support_team_id
metadata
payload
```

Nem todos os campos precisam estar preenchidos em todos os tipos de evento.

### 10.2 Imutabilidade

Um evento representa algo que ocorreu.

Exemplo:

```text
PIPELINE_FAILED às 10:02
DQ_RULE_FAILED às 10:05
DQ_RULE_PASSED às 11:00
```

O evento das 10:05 não deve ser atualizado para `PASS` às 11:00. Deve existir um novo evento.

### 10.3 Tipos conceituais de eventos

Exemplos:

```text
PROCESS_STARTED
PROCESS_SUCCEEDED
PROCESS_FAILED
PROCESS_CANCELLED
PROCESS_TIMEOUT

TASK_STARTED
TASK_SUCCEEDED
TASK_FAILED

DQ_RULE_RESULT

FRESHNESS_VIOLATION
FRESHNESS_RECOVERED
SLO_VIOLATION
SLO_RECOVERED
VOLUME_ANOMALY
DISTRIBUTION_ANOMALY
SCHEMA_DRIFT

CONTRACT_VIOLATION

GOVERNANCE_NON_COMPLIANCE
```

A taxonomia definitiva pode ser refinada durante implementação, preservando o envelope comum.

---

## 11. Data Quality — modelo de definição

### 11.1 Separação entre regra e resultado

A definição de uma regra deve existir independentemente da ferramenta que a executa.

```text
quality_rule
        |
        | executada por
        v
framework / notebook / engine
        |
        v
quality_result
```

A Observabilidade não exige que todas as regras sejam executadas pelo mesmo framework.

### 11.2 Atributos conceituais de `quality_rule`

```text
quality_rule_id
name
description
asset_id
columns
quality_dimension
rule_type
rule_definition
threshold_type
threshold_value
severity
is_blocking
owner
support_team_id
lifecycle_status
version
effective_from
effective_to
source
```

### 11.3 `is_blocking`

`is_blocking=true` descreve a política da regra.

A Observabilidade **não é responsável por interromper o pipeline**.

O mecanismo que executa a regra decide como aplicar o comportamento bloqueante.

A Observabilidade registra:

- a regra;
- seu caráter bloqueante;
- o resultado;
- o contexto de execução;
- o alerta ou condição derivada.

### 11.4 Exemplo YAML

```yaml
id: customer_not_null
asset: <asset_id>
dimension: completeness
rule:
  type: not_null
  column: customer_id
threshold:
  type: relative
  max_fail_rate: 0.001
severity: HIGH
blocking: true
support_team: commercial_data_quality
lifecycle_status: active
```

---

## 12. Data Quality — modelo de resultado

Um `quality_result` representa uma execução específica de uma regra.

```text
quality_result_id
quality_rule_id
asset_id
process_run_id
task_run_id
correlation_id
started_at
finished_at
status
checked_records
passed_records
failed_records
fail_rate
threshold_applied
baseline_reference
error_message
framework
framework_run_id
```

Status permitidos conceitualmente:

```text
PASS
FAIL
WARNING
ERROR
NOT_EXECUTED
UNKNOWN
```

`ERROR` significa que a validação não pôde ser executada corretamente e não deve ser interpretado automaticamente como dado inválido.

---

## 13. Quality Score

A metodologia corporativa de Quality Score continua **por definir**.

A solução deve preservar os dados necessários para cálculo futuro sem depender de reprocessamento da origem.

A proposta de `rule_score = 1 - fail_rate` e média ponderada pode ser utilizada somente como referência de discussão.

Nenhuma fórmula é mandatória neste documento.

---

## 14. SLI, SLO e SLA

### 14.1 Fonte das definições

SLIs são métricas observadas.

SLOs e SLAs representam expectativas e compromissos e devem ser definidos externamente à execução de observabilidade.

A Solução Conceitual exige que essas definições possuam uma representação operacional estruturada e auditável. Essa representação pode existir diretamente na plataforma de observabilidade ou em uma fonte externa declarativa, conforme a solução de implementação adotada.

### 14.2 Por que não usar apenas tags

Tags são adequadas para metadados simples, como:

```text
vc_criticality = critical
vc_refresh_frequency = daily
```

Um SLO real pode exigir:

```text
métrica
operador
threshold
unidade
janela de avaliação
agenda
horário esperado
timezone
grace period
severidade
vigência
```

Esse conteúdo deve ser tratado como configuração estruturada e versionada.

### 14.3 Exemplo de representação de freshness

```yaml
id: finance_orders_freshness
target:
  asset: <asset_id>

sli:
  type: freshness

objective:
  operator: less_than_or_equal
  value: 120
  unit: minutes

schedule:
  timezone: America/Sao_Paulo
  expected_by: "06:00"
  days: business_days

severity: HIGH
support_team: vcbr_data_support
```

### 14.4 Definições derivadas de Data Contracts

Quando um Data Contract possuir informações relevantes para observabilidade, um processo pode criar ou atualizar automaticamente as definições operacionais correspondentes na plataforma escolhida.

Exemplo:

```text
Data Contract
   frequency = daily
   available_by = 06:00
          |
          v
Generator / Integration
          |
          v
SLO Definition
na plataforma de observabilidade
```

A definição operacional gerada não substitui o Data Contract. Ela é uma **projeção operacional** utilizada pela capacidade de observabilidade.

A automação pode utilizar diretamente API/SDK da plataforma ou, quando houver benefício, gerar uma configuração declarativa intermediária em Git/YAML.

Independentemente do mecanismo, deve preservar referência à origem:

```yaml
source:
  type: data_contract
  reference: global.finance.orders
  version: "3"
```

---

## 15. Integração com Data Contracts no Hub/Spoke

### 15.1 Responsabilidade

A definição e governança dos Data Contracts pertencem à frente específica responsável.

A Observabilidade deve consumir metadados e eventos produzidos por essa frente.

### 15.2 Metadados no ativo

Quando aplicável, o ativo deve carregar referência suficiente ao contrato.

Exemplo conceitual:

```text
data_contract_id
data_contract_version
data_contract_scope
data_contract_status
```

Os nomes finais devem ser alinhados com a política de tags e com a frente de Data Contracts.

### 15.3 Contexto do evento

Eventos associados a ativos contratados podem carregar:

```text
asset_id
operating_unit
applicable_operating_units
data_scope
object_role
custodian_domain
vc_source_system
source_instance
authoritative_asset_id
contract_id
contract_version
contract_scope
```

### 15.4 Evento de violação

Quando a frente de Data Contracts detectar uma violação, poderá produzir ou disponibilizar algo conceitualmente equivalente a:

```text
event_type = CONTRACT_VIOLATION
contract_id
contract_version
asset_id
violation_id
violation_type
violation_severity
detected_at
description/reference
```

A Observabilidade recebe e correlaciona esse evento. A lógica que determinou a violação continua fora desta solução.

### 15.5 Exemplo VCEAA → Hub

```text
VCEAA
Produto certificado
asset_id = A
contract_id = GLOBAL_FINANCE_001
        |
        | sharing
        v
Hub
Objeto recebido
asset_id = B
authoritative_asset_id = A
contract_id = GLOBAL_FINANCE_001
```

O contrato fornece a interface comum, mas A e B continuam sendo instâncias técnicas distintas.

---

## 16. Rastreabilidade operacional e lineage

### 16.1 Rastreabilidade entre processo, execução e ativo

A solução deve preservar relações suficientes para permitir a navegação entre o processo observado, suas execuções técnicas e os ativos de dados envolvidos.

A relação conceitual mínima é:

```text
process
  |
process_run
  |
  +--> task/activity run, quando aplicável
  |
  +--> data asset
  |
  +--> quality results / SLO / observability events
```

Essa rastreabilidade é **obrigatória no estado-alvo atual** porque permite correlacionar uma falha operacional à execução que a produziu e ao ativo afetado, sem exigir a construção de um grafo completo de lineage.

Quando um fluxo atravessa mais de uma tecnologia, a solução deve preservar identificadores nativos suficientes para correlacionar as execuções relacionadas. A forma técnica de obter essa correlação pertence ao desenho de implementação de cada fonte.

### 16.2 Lineage de datasets como capacidade complementar

Relações upstream/downstream entre ativos podem ser úteis para investigação, diagnóstico e entendimento de dependências, mas **não são requisito obrigatório para a plataforma de Observabilidade manter por conta própria**.

Quando uma tecnologia já oferece lineage nativo, ou quando houver uma fonte corporativa ou externa de lineage, a Observabilidade pode consumir, referenciar ou apresentar essas informações sem duplicar obrigatoriamente o grafo em seu próprio repositório.

A adoção de armazenamento próprio de relações de lineage deve ocorrer apenas quando houver um caso de uso concreto que justifique o custo e a complexidade adicionais.

Portanto, a solução conceitual não exige:

- repositório corporativo próprio de lineage;
- reconstrução de lineage já mantido adequadamente pela plataforma de origem;
- persistência obrigatória de todas as relações upstream/downstream;
- adoção de um padrão ou produto específico de lineage.

Essa abordagem reconcilia o Nível 2 de lineage definido no Escopo (§19.2) — que faz parte do estado-alvo — com a decisão de não manter repositório próprio: o Nível 2 é considerado atendido por referência à fonte nativa de lineage, quando ela existir, e não por um grafo construído e mantido pela Observabilidade. Essa forma de atendimento tem uma lacuna conhecida e aceita, e não deve ser escondida: o lineage nativo de uma plataforma de origem — por exemplo, o lineage de tabelas do Unity Catalog — normalmente cobre relações dentro daquela própria tecnologia, mas não cobre lineage cross-technology, isto é, a cadeia completa que atravessa, por exemplo, um orquestrador, um motor de processamento e uma ferramenta de consumo. Essa parte da cadeia é coberta pela rastreabilidade operacional descrita na §16.1 — que correlaciona processo, execução e ativo mesmo entre tecnologias diferentes —, mas não constitui um grafo de lineage de datasets unificado. A lacuna é registrada aqui como conhecida e aceita, e cada arquitetura candidata deve declarar explicitamente como ela a trata, em vez de deixar a lacuna implícita.

### 16.3 Relações de identidade e compartilhamento

Relações entre um ativo recebido/compartilhado e seu objeto autoritativo continuam fazendo parte do modelo de identidade e governança, independentemente da existência de lineage técnico.

Exemplo:

```text
asset recebido
      |
authoritative_asset_id
      |
asset autoritativo
```

Essa relação não depende da construção de um grafo de transformação.

### 16.4 Capacidades opcionais

Podem ser consumidas ou adicionadas futuramente, quando houver benefício concreto:

- lineage upstream/downstream entre datasets;
- análise de impacto downstream;
- lineage em nível de coluna;
- grafo cross-platform completo.

Essas capacidades **não devem ser utilizadas como requisito obrigatório de implementação, comparação ou aceite** da plataforma de Observabilidade no estado-alvo atual.

---

## 17. Correlação fim a fim

A solução deve preservar identificadores nativos e corporativos suficientes para correlacionar:

```text
process
process_run
task_run
input_asset
output_asset
quality_rule
quality_result
slo_definition
observability_event
alert_condition
support_team
itsm_reference
contract_reference
```

### 17.1 Correlação entre processos

Quando uma execução aciona outra tecnologia, deve-se preservar identificadores que permitam correlação sempre que disponíveis.

Exemplo:

```text
ADF run
   |
   | correlation
   v
Databricks job run
   |
   v
DQ results
```

Não é obrigatório que todas as tecnologias utilizem o mesmo identificador nativo. A camada de normalização deve permitir registrar as relações conhecidas.

---

## 18. Condições operacionais e alertas

### 18.1 Evento não é condição

Um evento é um fato histórico.

Uma `alert_condition` representa uma condição operacional derivada que pode permanecer aberta por algum tempo.

Exemplo:

```text
10:05 DQ_RULE_FAILED
          |
          v
condition = OPEN

11:00 DQ_RULE_PASSED
          |
          v
condition = RESOLVED
```

### 18.2 Estado mínimo

A Observabilidade deve ser capaz de representar pelo menos:

```text
OPEN
RESOLVED
```

Estados adicionais podem existir conforme a implementação.

`OPEN` e `RESOLVED` são estados da condição, derivados da evidência observada: uma nova avaliação que reproduz a falha mantém a condição `OPEN`, e uma nova avaliação que comprova a recuperação move a condição para `RESOLVED`. Não existe um estado `IN_PROGRESS` na Observabilidade, nem resolução manual da condição — o negócio ou a equipe de suporte não "fecha" a condição por conta própria; a condição só muda de estado em função de evidência.

`ACKNOWLEDGED`, `IN_PROGRESS` e `CLOSED` pertencem ao ciclo de vida de um chamado no sistema ITSM e permanecem conceitualmente separados do estado `OPEN`/`RESOLVED` da condição. A Observabilidade não deve utilizá-los como se fossem estados internos obrigatórios.

### 18.3 Deduplicação por condição

Eventos recorrentes equivalentes podem ser agrupados por um `fingerprint` conceitual.

Exemplo de composição:

```text
condition_type
+ target_id
+ rule_id, quando aplicável
+ relevant_context
```

A composição concreta deve ser configurável.

O objetivo é distinguir:

```text
10 ocorrências da mesma condição
```

from

```text
10 condições diferentes
```

### 18.4 Correlação e supressão entre camadas

O `fingerprint` da §18.3 deduplica ocorrências repetidas *da mesma* condição. Ele não resolve um problema distinto: uma única causa raiz pode se manifestar como condições diferentes em camadas diferentes da arquitetura — por exemplo, a falha de uma atividade orquestradora, a falha do processamento que ela dispara, o resultado de qualidade que dependia desse processamento e a violação de freshness consequente do ativo. Sem um princípio de correlação entre camadas, cada uma dessas manifestações gera uma condição independente, com o risco de notificar múltiplas equipes para a mesma causa.

O modelo conceitual adota o princípio de **um incidente, um dono**: quando a relação de causa entre camadas é conhecida — por exemplo, por um identificador de correlação nativo que amarre a execução de uma tecnologia à execução que ela disparou em outra —, apenas uma condição deve ser tratada como incidente primário. As demais tornam-se detalhe técnico correlacionado, preservado no histórico e visível para investigação, mas sem gerar notificação nem tratamento independentes enquanto a condição primária permanecer aberta.

Qual camada é tratada como incidente primário quando a correlação existe é uma decisão que depende do desenho de orquestração corporativo vigente, e cada arquitetura candidata deve declarar explicitamente essa escolha e como ela se comporta quando a correlação não está disponível — por exemplo, quando uma execução não segue o caminho de disparo esperado e não pode ser correlacionada à execução que a precedeu. Nesse caso, a ausência de correlação não deve suprimir nem atrasar a geração da condição: a execução sem correlação identificável gera sua própria condição independente, com rastreabilidade reduzida, mas sem perda de observabilidade.

A supressão de uma condição consequente nunca a remove do histórico. Ela permanece registrada, com o vínculo à condição primária que a suprimiu, apenas sem gerar notificação ou tratamento próprios enquanto durar a supressão.

### 18.5 Ausência de sinal como origem de condição

Todo o modelo descrito até aqui neste capítulo é reativo: uma condição nasce a partir de um evento que chegou — um `PROCESS_FAILED`, um `DQ_RULE_RESULT` com `FAIL`. Isso não é suficiente para cobrir um tipo de falha relevante: a ausência de um sinal que era esperado e não chegou. O Escopo já lista "ausência de execução" como origem legítima de alerta e define `NOT_EXECUTED` como resultado possível de uma regra, mas nenhuma entidade do modelo descrito nas seções anteriores produz esses fatos por conta própria.

A solução deve incluir um avaliador periódico — e não apenas reativo a eventos — que compare a expectativa declarada de um ativo ou processo (`vc_refresh_frequency`, `vc_freshness_sla`, agenda esperada) contra a última ocorrência efetivamente observada, e que abra uma condição quando o sinal esperado não chegar dentro da janela tolerada. Esse avaliador consome os mesmos atributos de janela já previstos para um SLO real na §14.2 — grace period, timezone e dias úteis —, para não confundir ausência real de sinal com uma variação normal de horário ou um dia sem execução esperada.

Um cuidado conceitual importante é que o silêncio da fonte observada e o silêncio do próprio mecanismo de coleta são, à primeira vista, indistinguíveis: se o componente responsável por coletar e normalizar sinais parar de funcionar, todos os ativos que ele observa parecem simultaneamente silenciosos, mesmo que os processos de origem estejam executando normalmente. Por isso, o heartbeat do próprio mecanismo de coleta precisa ser tratado como uma condição de primeira classe, e sua ausência deve suprimir ou qualificar as condições de ausência de sinal que dependem dele — seguindo o mesmo princípio de correlação e supressão entre camadas descrito na §18.4 —, para não produzir uma tempestade de falsos positivos quando o problema real está na própria plataforma de observabilidade, e não nos dados observados.

`NOT_EXECUTED` é produzido por esse mesmo avaliador quando um conjunto de regras esperado para um determinado contexto de execução não foi executado — diferente de uma regra que foi executada e retornou `FAIL`, `WARNING` ou `ERROR`.

---

## 19. Roteamento para equipes de suporte

### 19.1 Princípio

A equipe responsável por tratar uma falha não deve ser inferida exclusivamente a partir do domínio ou da unidade operacional.

A mesma tabela pode possuir:

- suporte técnico de plataforma;
- suporte funcional de Data Quality;
- suporte regional;
- suporte global;
- suporte especializado por tecnologia.

`support_team` deve ser tratado como conceito operacional independente.

### 19.2 Equipe padrão e overrides

O modelo deve suportar herança.

```text
Evento / condição
      |
      v
Existe support_team específico na definição?
      |
     sim --------------------> usa equipe específica
      |
     não
      v
Existe support_team no objeto alvo?
      |
     sim --------------------> usa equipe do objeto
      |
     não
      v
Existe regra de roteamento por contexto?
      |
     sim --------------------> usa equipe da regra
      |
     não
      v
Equipe default da plataforma/operação
```

### 19.3 Processo e ativo podem ter equipes diferentes

Exemplo:

```text
Data Asset
    support_team = commercial_data_support

Pipeline
    support_team = data_platform_vcbr
```

Se o pipeline falhar tecnicamente:

```text
Falha de pipeline
    -> data_platform_vcbr
```

Se uma regra funcional do ativo falhar:

```text
Falha de DQ
    -> commercial_data_support
```

### 19.4 Override na regra de Data Quality

```yaml
quality_rule:
  id: customer_not_null
  support_team: commercial_data_quality
```

A falha dessa regra é direcionada para `commercial_data_quality`.

Outra regra pode não possuir override:

```yaml
quality_rule:
  id: order_id_unique
```

Nesse caso, herda a equipe de suporte do ativo.

### 19.5 Override em SLO

```yaml
slo:
  id: orders_freshness
```

Sem `support_team`, o SLO herda a equipe do ativo ou processo alvo.

Um SLO global pode definir:

```yaml
slo:
  id: global_finance_delivery
  support_team: global_data_operations
```

### 19.6 Exemplo de mesma tabela com falhas diferentes

```text
Tabela: vendas.pedidos

Falha do pipeline
    -> equipe: Data Platform VCBR

Falha da regra DQ "cliente obrigatório"
    -> equipe: Data Quality Comercial

Violação de freshness/SLO
    -> equipe: Data Platform VCBR
       ou equipe específica definida no SLO
```

Não é necessário criar obrigatoriamente três campos no ativo, como:

```text
pipeline_support_team
dq_support_team
slo_support_team
```

A solução utiliza **responsabilidade padrão + override quando necessário**.

### 19.7 Exemplos regionais e tecnológicos

Exemplo regional:

```text
vc_data_domain   = logistics
operating_unit = vcna
support_team  = support_data_vcna
```

Exemplo tecnológico:

```text
vc_data_domain   = finance
operating_unit = vcbr
support_team  = databricks_platform_support
```

O domínio e a unidade operacional enriquecem o contexto, mas não substituem o `support_team`.

### 19.8 `support_team` versus `itsm_assignment_group`

Esses campos devem ser separados.

`support_team` representa a identidade lógica corporativa da equipe.

`itsm_assignment_group` representa o mapeamento da equipe em um sistema ITSM específico.

Exemplo:

```yaml
support_team:
  id: data_platform_vcbr
  name: Data Platform VCBR
  itsm:
    system: servicenow
    assignment_group: GRP_DATA_PLATFORM_VCBR
```

Se o sistema ITSM mudar, a identidade da equipe não precisa mudar.

---

## 20. ITSM — separação de responsabilidades

### 20.1 Três conceitos distintos

A solução deve separar:

```text
observability_event
    fato ocorrido

alert_condition
    condição operacional observada

itsm_reference
    tratamento registrado em sistema externo
```

### 20.2 O que a Observabilidade consegue saber sozinha

Sem integração com ITSM, a Observabilidade consegue determinar, por exemplo:

```text
OPEN
RESOLVED
```

porque esses estados derivam dos próprios sinais observados.

### 20.3 O que depende do ITSM

Estados como:

```text
ACKNOWLEDGED
IN_PROGRESS
RESOLVED
CLOSED
```

só podem ser conhecidos de forma confiável quando houver integração ou sincronização com o ITSM.

---

## 21. MVP — Control-M e ServiceNow

### 21.1 Objetivo do MVP

No estágio inicial, o objetivo não é abrir um chamado individual para cada erro.

O objetivo é informar a uma equipe de suporte que existem **uma ou mais inconsistências ativas sob sua responsabilidade**.

A equipe acessa a solução de observabilidade, analisa as inconsistências e finaliza o chamado quando a situação estiver normalizada.

### 21.2 Agrupamento por equipe

Exemplo:

```text
Equipe Suporte Logística
    |- DQ tabela A - FAIL
    |- Freshness tabela B - FAIL
    |- Pipeline C - FAIL
             |
             v
       estado de inconsistência
             |
             v
       job Control-M
             |
             v
       chamado ServiceNow
```

Várias inconsistências podem coexistir no mesmo chamado operacional da equipe.

### 21.3 Jobs de verificação por equipe

Pode existir um job parametrizado executado separadamente para cada equipe.

Exemplo:

```text
check_observability_inconsistencies
    support_team=vcbr_data_support

check_observability_inconsistencies
    support_team=vcna_data_support

check_observability_inconsistencies
    support_team=databricks_platform_support
```

O processo deve:

1. consultar inconsistências ativas para a equipe;
2. se não existirem inconsistências, concluir com sucesso;
3. se existirem inconsistências relevantes, retornar falha controlada contendo contexto suficiente para suporte;
4. o Control-M utiliza essa falha para acionar o processo corporativo existente com ServiceNow.

### 21.4 Chamados repetidos

No MVP, **a prevenção de novos chamados para falhas recorrentes do mesmo job é responsabilidade do Control-M**, utilizando o comportamento já existente na operação corporativa.

A solução de Observabilidade não precisa manter estado adicional apenas para deduplicar chamados do Control-M.

### 21.5 Limitação do MVP

O Control-M abre ou gerencia o chamado no ServiceNow, mas a Observabilidade não recebe necessariamente:

```text
itsm_ticket_id
itsm_status
acknowledgment
resolution
```

Portanto, o modelo pode registrar:

```text
itsm_system = servicenow
itsm_ticket_id = null
itsm_integration_mode = control_m
```

Não deve ser criado artificialmente um número de chamado desconhecido.

### 21.6 Conteúdo da falha do job

A mensagem de falha deve permitir que a equipe localize rapidamente os problemas.

Exemplo conceitual:

```text
Equipe: VCNA Data Support
Inconsistências ativas: 4
Severidade máxima: CRITICAL
Domínios: Logistics, Finance
Consulta de detalhes: <referência para painel/consulta>
```

A mensagem não deve transportar dados sensíveis ou listas excessivamente grandes de registros inválidos.

---

## 22. Evolução futura — integração direta com ITSM

Uma evolução futura pode substituir ou complementar o fluxo Control-M por integração direta.

```text
Alert Condition
      |
      v
ITSM Integration
      |
      v
ServiceNow / outro ITSM
      |
      v
itsm_ticket_id
      |
      v
sincronização de status
```

Nesse estágio, passa a ser possível:

- criar chamado individual por condição quando desejável;
- agrupar condições de forma explícita;
- obter número do chamado;
- atualizar chamado existente;
- consultar acknowledgment;
- sincronizar resolução e encerramento;
- relacionar histórico de observabilidade ao ciclo ITSM.

### 22.1 Modelo genérico

```text
itsm_system
itsm_ticket_id
itsm_ticket_type
itsm_status
itsm_assignment_group
integration_mode
created_at
last_sync_at
```

Esses campos não devem ser obrigatórios nos eventos originais, porque o ticket pode ser criado depois que o evento ocorreu.

---

## 23. Regras de alertamento

As regras responsáveis por transformar sinais em condições operacionais devem ser estruturadas, auditáveis e versionáveis. Elas podem ser definidas diretamente na plataforma adotada ou por configuração externa como código.

Exemplo conceitual:

```yaml
id: dq_blocking_failure
source:
  event_type: DQ_RULE_RESULT
condition:
  status: FAIL
  blocking: true
severity: CRITICAL
routing:
  support_team: inherit
```

Outro exemplo:

```yaml
id: pipeline_failure
source:
  event_type: PROCESS_FAILED
severity: HIGH
routing:
  support_team: process
```

### 23.1 Fingerprint

Cada regra pode definir quais atributos participam da deduplicação.

Exemplo:

```yaml
fingerprint:
  fields:
    - condition_type
    - asset_id
    - quality_rule_id
```

A implementação final pode utilizar outro mecanismo equivalente.

---

## 24. Severidade e criticidade

A solução deve preservar separação entre:

```text
asset_criticality
rule_severity
condition_severity
incident_priority
```

`asset_criticality`, `rule_severity` e `condition_severity` utilizam a mesma escala `CRITICAL | HIGH | MEDIUM | LOW`. Manter uma escala única para os três conceitos permite compará-los e combiná-los ao calcular a severidade final de uma condição, sem exigir tabelas de conversão entre convenções diferentes.

`incident_priority`, por outro lado, pertence ao domínio do ITSM. Ele não deve ser derivado automaticamente da escala acima: a prioridade de um chamado no ServiceNow considera fatores adicionais do processo de incidentes (impacto declarado, urgência, políticas de atendimento) e é atribuída pelo próprio ITSM ou pela equipe que trata o chamado, não calculada pela Observabilidade a partir de `condition_severity`.

A severidade final de uma condição pode utilizar múltiplos fatores, como:

- criticidade do ativo;
- severidade da regra;
- blocking;
- escopo afetado;
- recorrência;
- tipo da falha.

A política definitiva de cálculo pode ser configurada posteriormente.

---

## 25. Métricas e visões derivadas

As métricas devem ser calculadas a partir das entidades e eventos normalizados.

### 25.1 Operação

```text
success_rate
failure_rate
execution_count
failure_count
average_duration
p95_duration
p99_duration
last_success_at
sla_slo_compliance
```

### 25.2 Data Quality

```text
rules_applicable
rules_executed
rules_passed
rules_failed
rules_error
rules_not_executed
blocking_failures
checked_records
failed_records
fail_rate
coverage
```

### 25.3 Data Observability

```text
freshness
freshness_violations
volume_anomalies
distribution_anomalies
schema_drift_events
```

### 25.4 Governança

```text
assets_without_owner
assets_without_steward
assets_without_required_metadata
assets_with_dq_rules
dq_coverage
```

### 25.5 Alertas e operação

```text
open_conditions
resolved_conditions
conditions_by_support_team
conditions_by_severity
conditions_by_domain
conditions_by_operating_unit
```

### 25.6 ITSM

No MVP, as métricas ITSM são limitadas porque não há retorno direto do ServiceNow.

No estado-alvo com integração direta, podem incluir:

```text
tickets_created
acknowledgment_time
resolution_time
reopened_tickets
open_tickets
```

---

## 26. Persistência conceitual

A solução deve distinguir pelo menos três classes de informação.

### 26.1 Definições

Exemplos:

```text
assets
quality rules
SLOs
alert rules
support routing
```

A fonte autoritativa de cada categoria deve ser definida pela implementação. Pode ser a própria plataforma de observabilidade, um repositório externo declarativo ou uma fonte corporativa integrada. Deve existir uma única responsabilidade clara por definição para evitar divergência de estado.

### 26.2 Histórico imutável

Exemplos:

```text
process runs
task runs
quality results
observability events
contract violation events
```

### 26.3 Estado derivado

Exemplos:

```text
active alert conditions
latest SLO evaluation
latest freshness state
current support routing
```

A implementação pode materializar estados para desempenho, mas o histórico deve permitir reconstrução quando tecnicamente aplicável.

---

## 27. Segurança e privacidade

Eventos e alertas devem carregar contexto suficiente para investigação sem transportar dados de negócio desnecessários.

Devem ser evitados:

- registros completos contendo dados pessoais;
- credenciais;
- secrets;
- payloads de origem sem necessidade operacional;
- grandes listas de registros inválidos em tickets.

Quando necessário, o evento pode armazenar uma referência para um conjunto de evidências protegido.

Exemplo:

```text
evidence_reference = <local protegido>
failed_records = 1523
```

em vez de armazenar os 1.523 registros no evento.

---

## 28. Extensibilidade por adapters

Cada tecnologia deve possuir um mecanismo de tradução para o modelo canônico.

```text
ADF -----------\
Databricks -----\
DQ Framework ----> canonical model
AWS Glue --------/
Future Tool -----/
```

O adapter é responsável por:

- ler o identificador nativo;
- mapear status;
- preservar IDs de origem;
- gerar a identidade lógica correta;
- aplicar o envelope canônico;
- enriquecer o evento quando os metadados estiverem disponíveis.

Adicionar uma nova fonte não deve exigir mudança nas entidades centrais.

---

## 29. Relação com cenários de implementação em avaliação

Esta Solução Conceitual não escolhe uma arquitetura.

Cada cenário deverá demonstrar como implementa, no mínimo:

- `asset_id` e identidade cross-region/cross-platform;
- entidades canônicas;
- envelope de eventos;
- DQ rules e DQ results;
- gestão auditável e versionável das configurações operacionais;
- SLO evaluation;
- integração com metadados de governança;
- Data Contract references;
- rastreabilidade operacional entre processo, execução e ativo;
- capacidade de consumir ou referenciar lineage nativo/externo quando disponível;
- alert conditions;
- support routing;
- MVP Control-M;
- evolução ITSM;
- histórico e métricas.

A comparação deve ocorrer sobre requisitos equivalentes, sem introduzir requisitos exclusivos para favorecer uma das alternativas.

---

## 30. Fluxos de referência

### 30.1 Falha técnica de pipeline

```text
Pipeline
   |
   v
PROCESS_FAILED
   |
   v
Normalização + metadados
   |
   v
Alert Rule
   |
   v
Alert Condition OPEN
   |
   v
support_team do processo
   |
   v
notificação / acionamento operacional
   |
   v
Control-M, quando aplicável ao pipeline crítico
   |
   v
abertura ou manutenção do chamado no ServiceNow
```

No modelo operacional atual, pipelines críticos podem ser monitorados pelo Control-M de forma que a falha do job gere automaticamente o acionamento do processo corporativo de incidente no ServiceNow. Nesse fluxo, o Control-M atua como mecanismo de **notificação e abertura/manutenção do chamado**, e não como consumidor que consulta as inconsistências da plataforma de Observabilidade.

Esse comportamento representa o estado atual e pode ser revisto em evoluções futuras. Independentemente do mecanismo de acionamento, a falha deve preservar o `support_team` responsável e o contexto necessário para direcionar o tratamento à equipe correta.

### 30.2 Falha de Data Quality

```text
Quality Rule
   |
   v
Engine executa regra
   |
   v
Quality Result FAIL
   |
   v
DQ_RULE_RESULT
   |
   v
Alert Condition OPEN
   |
   v
support_team da regra
   |
   +-- ausente --> support_team do ativo
```

### 30.3 Recuperação de Data Quality

```text
DQ Rule FAIL
    |
    v
Condition OPEN

execução posterior
    |
    v
DQ Rule PASS
    |
    v
Condition RESOLVED
```

### 30.4 Violação de SLO

```text
SLO Definition
      |
      v
SLI measurement
      |
      v
Evaluation
      |
      +-- compliant --> no violation
      |
      +-- breached --> SLO_VIOLATION
                           |
                           v
                    Alert Condition
                           |
                           v
                    support routing
```

### 30.5 Data Contract gerando configuração observável

```text
Data Contract
    |
    | owned by Data Contract initiative
    v
Generator / Integration
    |
    +----------------------------+
    |                            |
    v                            v
API/SDK da plataforma       Git/configuração como código
    |                            |
    +-------------+--------------+
                  |
                  v
        Observability runtime
```

A integração direta com a plataforma é preferível quando não houver necessidade de um artefato intermediário. Git/YAML ou mecanismo equivalente permanece como opção quando agregar governança, aprovação, automação ou portabilidade.

### 30.6 MVP de chamado por equipe

```text
Equipe A
  condition 1 OPEN
  condition 2 OPEN
  condition 3 OPEN
          |
          v
Control-M verification job
          |
          v
job fails with summary
          |
          v
Control-M manages ServiceNow incident
```

O chamado representa a existência de inconsistências para a equipe, não cada evento individual.

---

## 31. Decisões conceituais consolidadas

1. O modelo canônico será independente de tecnologia.
2. Haverá envelope comum de eventos e payload específico por tipo.
3. Regras de Data Quality serão separadas de seus resultados.
4. `is_blocking` não torna a Observabilidade responsável por interromper pipelines.
5. Ativos sem metadados completos continuam sendo observados.
6. Eventos históricos são imutáveis.
7. Condições ativas são estado derivado e separadas dos eventos.
8. A prevenção de chamados repetidos do MVP é responsabilidade do Control-M.
9. O MVP pode possuir jobs de verificação por equipe de suporte.
10. Um chamado do MVP pode representar múltiplas inconsistências de uma equipe.
11. O número do chamado ITSM pode ser desconhecido no MVP.
12. Integração ITSM direta é evolução futura.
13. `support_team` é conceito corporativo e `itsm_assignment_group` é mapeamento de integração.
14. Processos, ativos, regras e SLOs podem possuir equipes de suporte diferentes.
15. O roteamento utiliza herança e overrides em vez de três equipes obrigatórias por ativo.
16. Configurações operacionais devem ser estruturadas, auditáveis, versionáveis e automatizáveis, sem obrigatoriedade de Git/YAML.
17. A própria plataforma de observabilidade pode ser fonte autoritativa para configurações que ela governa adequadamente; Git/configuração como código permanece como opção quando houver benefício concreto.
18. Data Contracts podem gerar ou atualizar diretamente configurações na plataforma por API/SDK ou por configuração como código; o Data Contract continua sendo a fonte autoritativa da expectativa contratual.
19. A identidade do ativo usa uma autoridade de namespace e não depende de workspace.
20. No Databricks/Unity Catalog, o Metastore ID é um candidato natural a `namespace_authority`.
21. Objetos compartilhados/recebidos podem ter `asset_id` próprio e referência ao ativo autoritativo.
22. VCBR, Itacamba, VCNA e VCEAA podem coexistir no mesmo modelo mesmo com diferentes subscriptions, workspaces e metastores.
23. O modelo também deve suportar Databricks em AWS e plataformas não Databricks.
24. A rastreabilidade operacional entre processo, execução e ativo faz parte do estado-alvo atual.
25. A plataforma não é obrigada a manter um repositório próprio de lineage; lineage upstream/downstream, análise de impacto, column-level e grafos cross-platform podem ser consumidos de fontes nativas/externas ou evoluídos quando houver benefício concreto.
26. Quality Score permanece por definir.

---

## 32. Itens em aberto

### 32.1 Quality Score

A metodologia definitiva de Quality Score permanece em aberto.

### 32.2 Política final de severidade

O modelo suporta criticidade, severidade de regra e severidade de condição, mas a fórmula corporativa final de combinação permanece a definir.

### 32.3 Taxonomia definitiva de equipes de suporte

O conceito `support_team` está definido, porém o catálogo corporativo de equipes e os respectivos mapeamentos para `itsm_assignment_group` deverão ser formalizados.

### 32.4 Modelo final de administração das configurações

Deverá ser definido por categoria de configuração o sistema autoritativo, o modelo de versionamento, os mecanismos de aprovação e as APIs ou formatos de automação. Caso Git/configuração como código seja adotado, o schema e a estrutura do repositório deverão ser definidos na fase de implementação.

### 32.5 Interface definitiva com Data Contracts

Os campos exatos, nomes de tags e mecanismo de sincronização das definições operacionais deverão ser alinhados com a frente responsável por Data Contracts e com a política corporativa de governança. A integração poderá ocorrer diretamente por API/SDK ou por artefato declarativo intermediário.

### 32.6 Regras exatas de identidade por tecnologia

A composição canônica está definida, mas adapters específicos deverão documentar quais identificadores nativos representam `platform_account` e `namespace_authority` em cada tecnologia.

---

## 33. Próximo passo

Com o Escopo e a Solução Conceitual consolidados, Cen1 e Cen2 podem ser avaliados contra uma mesma referência.

A próxima etapa deve produzir uma matriz comparativa que avalie, para cada arquitetura:

- atendimento funcional;
- aderência ao modelo canônico;
- capacidade de identidade e correlação;
- Data Quality;
- SLOs;
- Data Contracts;
- rastreabilidade entre processo, execução e ativo;
- aproveitamento de lineage nativo/externo quando relevante;
- alertas;
- roteamento de suporte;
- MVP Control-M;
- evolução ITSM;
- portabilidade;
- esforço operacional;
- complexidade;
- custos;
- riscos e lacunas.
