# Resumo executivo

## 1. Objetivo

A iniciativa de **Observabilidade e Data Quality** tem como objetivo criar uma capacidade corporativa para acompanhar, de forma integrada, a execução dos processos de dados e a confiabilidade dos dados entregues ao negócio.

A solução deve consolidar sinais hoje distribuídos entre diferentes tecnologias e permitir responder, de forma rastreável, perguntas como:

- qual processo ou pipeline executou;
- se a execução foi concluída com sucesso ou falhou;
- quais ativos de dados foram consumidos e produzidos;
- quais regras de qualidade foram executadas e quais falharam;
- se os dados foram disponibilizados dentro dos níveis de serviço esperados;
- qual equipe é responsável pelo tratamento de uma falha;
- quais alertas ou condições permanecem ativos;
- quais ativos estão relacionados por lineage;
- quais violações de Data Contracts foram reportadas pela frente responsável.

O ambiente atual é predominantemente **Azure e Databricks**, com **Control-M** para orquestração corporativa e **ServiceNow** para gestão de incidentes. Entretanto, o modelo foi definido de forma agnóstica para permitir expansão para outras regiões, clouds e tecnologias.

---

## 2. Problemas que a iniciativa busca resolver

Atualmente, execução, Data Quality, metadados, alertas e incidentes são observados em ferramentas e contextos diferentes. Isso dificulta uma visão integrada da confiabilidade dos dados.

Os principais problemas são:

- ausência de uma visão única das execuções de pipelines e jobs;
- dificuldade de correlacionar falhas técnicas com ativos de dados e problemas de qualidade;
- baixa padronização e ausência de histórico corporativo das regras de Data Quality;
- dificuldade para medir freshness, SLOs e SLAs;
- alertas e mecanismos de tratamento distribuídos;
- ownership e responsabilidade operacional nem sempre facilmente identificáveis;
- baixa maturidade de lineage entre datasets;
- dificuldade de produzir indicadores e tendências comparáveis entre ativos, domínios e unidades operacionais.

---

## 3. Princípios

A proposta foi construída sobre os seguintes princípios:

1. **Independência tecnológica** — o modelo corporativo não deve depender de uma ferramenta específica.
2. **Modelo comum** — ADF, Databricks, frameworks de Data Quality e futuras tecnologias devem ser traduzidos para conceitos comuns.
3. **Histórico auditável** — execuções, resultados de qualidade e eventos relevantes devem ser preservados historicamente.
4. **Governança por metadados** — ownership, domínio, criticidade, unidade operacional e demais classificações devem ser consumidos das políticas corporativas.
5. **Configuração separada da execução** — regras de qualidade, SLOs, alertas e roteamento devem ser definidos separadamente dos resultados gerados.
6. **Extensibilidade** — novas fontes e novas clouds devem ser incorporadas por adapters sem alterar o modelo central.
7. **Segurança por padrão** — eventos e alertas devem transportar metadados suficientes para investigação, evitando exposição desnecessária de dados de negócio ou dados pessoais.

---

## 4. Capacidades previstas

### 4.1 Observabilidade operacional

A solução deverá consolidar informações de execução de pipelines, jobs e tarefas, incluindo:

- sucesso, falha, cancelamento e timeout;
- início, fim e duração;
- recorrência de falhas;
- identificação da etapa ou tarefa com problema;
- correlação entre processos e ativos produzidos ou consumidos;
- histórico e tendências de execução.

### 4.2 Data Observability

Além do sucesso técnico do pipeline, a solução deverá observar o comportamento do dado, incluindo:

- freshness;
- timeliness;
- volume;
- ausência inesperada de dados;
- alterações de distribuição;
- anomalias;
- schema drift quando disponível;
- comportamento fora de baseline;
- sinais de Data Contracts fornecidos pela frente responsável.

### 4.3 Data Quality

As regras de Data Quality serão representadas independentemente da ferramenta utilizada para executá-las.

As principais dimensões consideradas são:

- completude;
- unicidade;
- validade;
- consistência;
- integridade referencial;
- acurácia;
- conformidade;
- freshness;
- timeliness;
- volume;
- distribuição e anomalia.

A definição de uma regra será separada do seu resultado. Os resultados deverão distinguir, no mínimo, `PASS`, `FAIL`, `WARNING`, `ERROR`, `NOT_EXECUTED` e `UNKNOWN`. Um erro técnico na execução de uma regra não deve ser confundido automaticamente com dado inválido.

### 4.4 Geração automática de regras a partir de metadado

Uma capacidade central do projeto é traduzir metadados já declarados no catálogo de dados — tags de tabela e coluna, chaves primárias e estrangeiras, tipos de dado — em regras e expectativas operacionais, sem exigir cadastro manual de cada regra. Uma tag de frequência de atualização pode gerar automaticamente uma expectativa de freshness; uma chave primária pode gerar uma regra de unicidade; uma tag semântica de coluna (por exemplo, indicando que um campo é um CPF) pode gerar uma família de validações padronizadas. A arquitetura de desenvolvimento interno avaliada (Cen2) detalha esse componente — chamado Metadata Collector / Rule Generator — incluindo a política de precedência entre regra manual e regra derivada, e a definição de quem executa cada tipo de regra derivada.

### 4.5 Detecção de ausência de sinal

A arquitetura é, em sua maior parte, reativa a sinais que chegam. Um componente complementar — o avaliador temporal, também detalhado no Cen2 — compara periodicamente a expectativa declarada de um ativo (frequência de atualização, prazo de disponibilização) contra a última ocorrência observada, e abre uma condição quando o sinal esperado não chega dentro da janela tolerada. Esse componente também precisa distinguir a ausência de sinal por falha da fonte de dados da ausência de sinal por falha do próprio mecanismo de coleta — o segundo caso não deve gerar uma tempestade de condições individuais para cada ativo afetado.

---

## 5. Modelo conceitual

A solução utiliza um conjunto comum de entidades para permitir correlação entre diferentes tecnologias. Entre as principais estão:

- processo e execução de processo;
- tarefa e execução de tarefa;
- ativo de dados;
- regra de qualidade e resultado de qualidade;
- definição de SLO;
- evento de observabilidade;
- condição de alerta;
- equipe de suporte;
- referência ITSM;
- relação de lineage;
- referência a Data Contract.

A visão lógica é:

```text
Fontes e metadados
        |
        v
Definições versionadas
        |
        v
Eventos imutáveis
        |
        v
Correlação e estado operacional
        |
        v
Dashboards, métricas, Control-M, ITSM e outros consumidores
```

Um ponto importante é a separação entre **evento** e **condição**. Um evento registra um fato ocorrido. Uma condição representa um problema que permanece ativo até que os sinais indiquem recuperação.

---

## 6. Identidade dos ativos e modelo global Hub/Spoke

A identidade de uma tabela ou dataset não deve depender apenas do nome físico. Isso é necessário porque a arquitetura corporativa pode possuir diferentes subscriptions, workspaces e metastores regionais, além de compartilhamento entre Hub e Spokes.

A identidade lógica considera, conceitualmente:

```text
plataforma
+ conta da plataforma
+ autoridade do namespace
+ namespace
+ tipo do objeto
+ nome do objeto
```

No Databricks com Unity Catalog, o **Metastore ID** é um candidato natural para representar a autoridade do namespace.

Essa abordagem permite distinguir corretamente, por exemplo:

- VCBR e Itacamba consumindo o mesmo objeto compartilhado dentro do metastore brasileiro;
- VCNA operando um objeto de mesmo nome em outro metastore regional;
- VCEAA publicando um produto certificado para o Hub;
- Databricks executado futuramente em AWS;
- uma futura plataforma AWS sem Databricks.

Um objeto recebido por compartilhamento pode possuir identidade técnica própria, mas deve manter referência ao ativo autoritativo que o originou.

---

## 7. Governança e Data Contracts

A Observabilidade consumirá metadados definidos pela governança corporativa, mas não definirá quais tags são obrigatórias.

Entre os metadados relevantes estão domínio, unidade operacional, sistema e instância de origem, criticidade, owner, steward, escopo do dado, papel do objeto e referência ao objeto autoritativo.

Ativos sem metadados completos **continuam sendo observados**. A ausência de metadata pode ser registrada como não conformidade, em vez de impedir a entrada do ativo na plataforma.

Os **Data Contracts** pertencem a uma frente específica. A Observabilidade não será responsável por criar, versionar ou detectar primariamente violações de contratos.

Quando disponíveis, referências de contrato e eventos de violação deverão ser correlacionados aos ativos, execuções, regras, alertas e incidentes.

No modelo Hub/Spoke, os contratos também ajudam a formalizar a publicação de produtos certificados entre Spokes e Hub.

---

## 8. Configuração operacional estruturada e auditável

Regras operacionais deverão ser estruturadas, auditáveis, versionáveis e automatizáveis, incluindo:

- regras de Data Quality;
- SLOs;
- regras de alertamento;
- roteamento para equipes de suporte;
- outras configurações de observabilidade.

A definição de qual mecanismo administra cada categoria de configuração não está fixada em uma tecnologia única. Na arquitetura de desenvolvimento interno avaliada (Cen2), o PostgreSQL é a fonte autoritativa dessas configurações, administrado pela própria aplicação por API/UI, com histórico de versões próprio — não um repositório Git com YAML. Git/YAML, JSON ou Terraform continuam disponíveis como mecanismos válidos de import/export, bootstrap ou automação em massa, e não como fonte de verdade obrigatória. Se a organização optar por uma plataforma SaaS, a recomendação é usar as capacidades nativas de configuração da própria ferramenta sempre que atenderem aos requisitos corporativos, em vez de reconstruir uma camada de configuração externa apenas por preferência arquitetural.

Um processo poderá também ler Data Contracts e gerar ou atualizar automaticamente as definições operacionais correspondentes na plataforma escolhida, diretamente por API/SDK ou por uma configuração declarativa intermediária. Nesse caso, o Data Contract continua sendo a fonte original e a definição operacional gerada é apenas sua projeção operacional.

---

## 9. SLI, SLO e SLA

SLIs representam métricas observadas. SLOs e SLAs representam objetivos ou compromissos definidos externamente à execução da observabilidade.

A definição operacional desses objetivos deve possuir uma representação estruturada e versionada — na plataforma de observabilidade escolhida ou em uma fonte externa declarativa, conforme a categoria de configuração —, permitindo expressar elementos como:

- métrica observada;
- threshold;
- operador;
- unidade;
- janela de avaliação;
- agenda e horário esperado;
- timezone;
- período de tolerância;
- severidade;
- vigência.

Essa estrutura é mais adequada que utilizar apenas tags, pois um SLO pode possuir lógica e parâmetros complexos.

---

## 10. Roteamento para equipes de suporte

A responsabilidade funcional pelo dado e a responsabilidade operacional por uma falha são conceitos distintos.

Uma mesma tabela pode ter uma equipe tratando falhas técnicas do pipeline e outra tratando problemas funcionais de Data Quality.

Exemplo:

```text
Tabela: vendas.pedidos

Falha do pipeline
    -> Data Platform VCBR

Falha da regra de DQ "cliente obrigatório"
    -> Data Quality Comercial

Violação de freshness/SLO
    -> Data Platform VCBR
       ou equipe específica configurada no SLO
```

O modelo adotará **equipe padrão + override quando necessário**.

Assim:

- falha de pipeline utiliza normalmente a equipe definida para o processo;
- falha de Data Quality utiliza a equipe da regra ou, se não definida, herda a equipe do ativo;
- violação de SLO utiliza a equipe definida no SLO ou herda a responsabilidade do ativo/processo correspondente.

`support_team` representa a equipe lógica corporativa. O `itsm_assignment_group` representa apenas o mapeamento dessa equipe no ITSM atual.

---

## 11. Alertas, condições e incidentes

Eventos relevantes poderão gerar condições operacionais. A solução deverá identificar quando uma condição está aberta e quando foi resolvida.

Exemplo:

```text
DQ FAIL
   -> condição OPEN

execução posterior com PASS
   -> condição RESOLVED
```

`OPEN` e `RESOLVED` são estados da condição, derivados da evidência observada — não existe um estado intermediário de acompanhamento manual, e o negócio não "fecha" a condição por conta própria.

Condições equivalentes poderão ser agrupadas por fingerprint para evitar ruído e permitir identificar recorrência. Além disso, quando uma única causa raiz se manifesta como condições distintas em camadas diferentes da arquitetura — por exemplo, a falha de uma atividade orquestradora e a falha do processamento que ela dispara —, o modelo adota o princípio de **um incidente, um dono**: apenas uma condição é tratada como incidente primário, e as demais tornam-se detalhe técnico correlacionado, sem gerar notificação nem tratamento independentes.

A Observabilidade consegue determinar sozinha se a condição técnica está `OPEN` ou `RESOLVED`. Estados como acknowledgment, tratamento e fechamento pertencem ao sistema ITSM e somente poderão ser conhecidos de forma confiável quando existir integração com ele.

---

## 12. MVP operacional com Control-M e ServiceNow

O MVP aproveitará os mecanismos corporativos já existentes.

### Pipelines críticos

No modelo operacional atual, pipelines críticos podem ser monitorados pelo Control-M. Uma falha pode gerar automaticamente a abertura ou manutenção de chamado no ServiceNow.

O Control-M atua nesse caso como mecanismo de **notificação e acionamento operacional**.

### Problemas de qualidade e outras inconsistências

Para Data Quality, SLOs e outras condições, podem existir jobs de verificação por equipe de suporte.

Cada job consulta as inconsistências ativas da equipe e:

- termina com sucesso quando não existem inconsistências relevantes;
- retorna falha controlada quando existem problemas que precisam de tratamento;
- o Control-M utiliza essa falha para acionar ou manter o processo de incidente no ServiceNow.

Um chamado pode representar **várias inconsistências simultâneas de uma mesma equipe**, em vez de um chamado por problema individual.

O Control-M já é responsável por evitar a abertura de novos chamados a cada repetição da mesma falha do job.

A principal limitação do MVP é que a Observabilidade não recebe necessariamente o número ou o estado do chamado no ServiceNow.

---

## 13. Evolução futura de ITSM

Uma evolução posterior poderá implementar integração direta com ServiceNow ou outro ITSM.

Isso permitirá:

- obter o número do chamado;
- criar chamado individual por condição quando necessário;
- agrupar problemas de forma explícita;
- atualizar chamados existentes;
- sincronizar status, acknowledgment e resolução;
- relacionar o histórico da Observabilidade ao ciclo completo do incidente.

O modelo conceitual já foi preparado para essa evolução sem exigir redesenho estrutural.

---

## 14. Lineage e rastreabilidade

O estado-alvo atual contempla dois níveis:

**Nível 1 — rastreabilidade de execução:** relacionar processo, execução, ativos consumidos/produzidos e resultados de qualidade.

**Nível 2 — lineage de datasets:** registrar relações upstream e downstream entre ativos.

Exemplo:

```text
origem -> raw -> cleansed -> curated -> consumo
```

Análise de impacto downstream (nível 3) e lineage em nível de coluna (nível 4) são possibilidades futuras e **não fazem parte dos requisitos atuais de implementação ou aceite**.

---

## 15. Métricas e visão gerencial

A solução deverá permitir indicadores em diferentes perspectivas.

**Operação:** taxa de sucesso e falha, duração, P95/P99, recorrência e última execução bem-sucedida.

**Data Quality:** cobertura de regras, regras executadas, falhas, erros técnicos, falhas bloqueantes, registros inválidos e evolução temporal.

**Data Observability:** freshness, violações de freshness, anomalias de volume/distribuição e schema drift.

**Governança:** ativos sem owner/steward, ausência de metadados requeridos e cobertura de regras.

**Operação de suporte:** condições ativas e recuperadas, por severidade, domínio, unidade operacional e equipe responsável.

---

## 16. O que ainda está em aberto

Algumas decisões foram intencionalmente deixadas para fases posteriores:

- metodologia corporativa de **Quality Score**;
- política definitiva de cálculo/mapeamento de severidade;
- catálogo corporativo de `support_team` e mapeamento para grupos do ITSM;
- modelo final de administração das configurações operacionais por categoria (fonte autoritativa, versionamento, aprovação);
- interface definitiva entre Observabilidade e a frente de Data Contracts;
- desenho físico e tecnologia de implementação;
- integração direta com ITSM;
- níveis futuros de lineage;
- residência de metadados entre regiões e tenants (particularmente relevante para VCEAA).

Três decisões adicionais, específicas da arquitetura de desenvolvimento interno avaliada (Cen2), permanecem pendentes e dependem de confirmação do usuário — cada uma tem uma recomendação registrada nos documentos correspondentes, marcada explicitamente como proposta a confirmar, não como decisão tomada:

1. **Dono do incidente na correlação entre uma atividade ADF e o job Databricks que ela dispara** — quando a correlação é conhecida, qual dos dois níveis é tratado como incidente primário.
2. **Se a detecção estatística de anomalia (variação de volume, distribuição, schema drift) entra no MVP ou fica para Fase 2.**
3. **Se VCNA e VCEAA operam no mesmo tenant Azure do VCBR** — a resposta determina a topologia de coleta de eventos do ADF por região/tenant.

---

## 17. Próxima etapa

O Escopo e a Solução Conceitual estabelecem uma base comum para avaliar os cenários de implementação.

A próxima etapa é comparar as arquiteturas candidatas utilizando os mesmos critérios, verificando como cada uma atende, entre outros pontos:

- modelo canônico e identidade de ativos;
- captura e histórico de eventos;
- Data Quality;
- SLOs e Data Observability;
- integração com governança e Data Contracts;
- lineage níveis 1 e 2;
- roteamento de suporte;
- integração com Control-M e evolução ITSM;
- extensibilidade e portabilidade;
- esforço operacional;
- complexidade;
- custos e riscos.

A escolha da tecnologia deve ocorrer **depois** dessas definições, evitando que requisitos sejam moldados para favorecer previamente uma alternativa.
