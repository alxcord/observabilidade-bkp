# Escopo

## 1. Objetivo

Este documento define o escopo funcional, conceitual e não funcional para uma capacidade corporativa de Observabilidade e Data Quality.

O objetivo é estabelecer **o que a plataforma deve ser capaz de observar, medir, correlacionar, governar e disponibilizar**, independentemente da tecnologia que venha a ser adotada para implementação.

A solução deve permitir rastreabilidade desde a execução de processos de dados até a condição do dado disponibilizado ao negócio, combinando sinais operacionais, qualidade, ownership, níveis de serviço, alertas, incidentes e indicadores gerenciais. Quando disponíveis, violações de Data Contracts produzidas pela frente responsável também devem poder ser correlacionadas a esse contexto.

O ambiente atual é predominantemente Azure, mas o escopo deve permanecer suficientemente genérico para suportar futuras fontes, tecnologias, nuvens e mecanismos de processamento sem necessidade de redefinição dos conceitos centrais.

---

## 2. Princípios do escopo

A capacidade de Observabilidade e Data Quality deve seguir os seguintes princípios:

1. **Independência tecnológica** — os requisitos não devem depender de uma ferramenta específica.
2. **Visão corporativa** — sinais de múltiplos motores, orquestradores e plataformas devem poder ser consolidados em um modelo comum.
3. **Rastreabilidade** — deve ser possível relacionar execução, ativo de dados, qualidade, nível de serviço, alerta e incidente e, quando disponível, referências ou violações de Data Contracts provenientes da frente responsável.
4. **Governança por metadados** — ownership, criticidade, domínio e demais classificações devem ser obtidos de fontes corporativas de governança.
5. **Qualidade explícita** — regras, resultados e métricas de qualidade devem ser modelados de forma estruturada, auditável e comparável.
6. **Histórico e tendência** — a plataforma deve permitir análise temporal, não apenas visualização do estado corrente.
7. **Automação operacional** — eventos relevantes devem poder alimentar alertas, workflows e processos corporativos de incidentes.
8. **Extensibilidade** — novas fontes e tecnologias devem poder ser incorporadas sem alterar o modelo conceitual central.
9. **Segurança e privacidade por padrão** — dados sensíveis não devem ser expostos desnecessariamente em eventos, logs ou alertas.
10. **Separação entre escopo e solução** — este documento define capacidades e requisitos. Tags específicas, schemas, ferramentas, integrações técnicas e arquitetura pertencem à Solução Conceitual e aos desenhos de implementação.

---

## 3. Contexto atual

Atualmente os sinais necessários para operação e governança de dados encontram-se distribuídos entre diferentes tecnologias e consoles.

No contexto atual, existem principalmente:

- pipelines e processos de ingestão/orquestração;
- jobs e tarefas de processamento;
- tabelas e datasets;
- testes e validações de qualidade;
- catálogos e metadados de governança;
- ferramentas corporativas de agendamento e orquestração;
- ferramentas corporativas de gestão de incidentes.

O ambiente atual utiliza Azure como principal plataforma de nuvem. ServiceNow é utilizado para gestão corporativa de chamados e incidentes, e Control-M é utilizado como orquestrador corporativo de jobs.

Essas tecnologias representam o contexto atual, mas não constituem restrição arquitetural para este escopo. A capacidade deve ser definida de forma que mecanismos equivalentes possam ser utilizados no futuro.

---

## 4. Problemas a resolver

A plataforma deve endereçar, de forma integrada, os seguintes problemas:

- ausência de um ponto único de visão sobre execuções;
- dificuldade para correlacionar falhas técnicas e problemas de qualidade;
- baixa padronização das regras de Data Quality;
- ausência de histórico central de resultados de qualidade;
- dificuldade para calcular indicadores comparáveis entre ativos e domínios;
- ownership incompleto ou de difícil consulta;
- alertas configurados de forma descentralizada;
- baixa rastreabilidade entre evento detectado e incidente operacional;
- dificuldade para avaliar SLA, SLO e freshness;
- ausência ou baixa maturidade de lineage;
- dificuldade para correlacionar mudanças estruturais e violações de Data Contracts quando esses sinais forem disponibilizados pela frente responsável;
- necessidade futura de evoluir para análise de impacto downstream, explicitamente fora do escopo atual;
- dificuldade para produzir scorecards de confiabilidade para operação, governança e negócio.

---

## 5. Personas e principais casos de uso

### 5.1 Engenharia de Dados

Deve ser capaz de:

- identificar pipelines, jobs ou tarefas com falha;
- analisar duração e recorrência de erros;
- consultar histórico de execuções;
- identificar o ativo de dados afetado;
- correlacionar falhas com resultados de qualidade;
- correlacionar mudanças estruturais e violações de Data Contracts quando disponibilizadas pela frente responsável;
- identificar dependências upstream e downstream quando disponíveis.

### 5.2 Operação / Sustentação

Deve ser capaz de:

- visualizar eventos críticos e alertas ativos;
- identificar severidade e impacto;
- localizar responsável pelo ativo;
- evitar alertas duplicados;
- acompanhar abertura, atualização e encerramento de incidentes;
- consultar evidências técnicas suficientes para diagnóstico inicial.

### 5.3 Data Governance / Data Stewardship

Deve ser capaz de:

- identificar ativos sem ownership ou metadados obrigatórios conforme política corporativa;
- acompanhar cobertura de regras de qualidade;
- consultar evolução da qualidade;
- consultar violações de Data Contracts associadas aos ativos, quando disponibilizadas pela frente responsável;
- analisar ativos com baixa confiabilidade ou recorrência de problemas.

### 5.4 Data Owner / Negócio

Deve ser capaz de:

- entender o estado de confiabilidade dos dados sob sua responsabilidade;
- consultar SLA/SLO e qualidade;
- identificar problemas relevantes e seu impacto;
- acompanhar evolução histórica e reincidência.

### 5.5 Liderança

Deve ser capaz de:

- consultar indicadores de confiabilidade da plataforma;
- comparar domínios, produtos ou ativos;
- acompanhar tendências de falha, qualidade e cumprimento de níveis de serviço;
- identificar riscos recorrentes e áreas que demandam priorização.

---

## 6. Modelo conceitual de entidades observáveis

A plataforma deve ser capaz de representar, no mínimo, os seguintes conceitos.

### 6.1 Processo

Unidade lógica de processamento ou orquestração, como pipeline, job, workflow ou fluxo equivalente.

### 6.2 Execução de processo

Instância de execução de um processo em determinado momento.

Deve possuir, quando disponível:

- identificador da execução;
- processo associado;
- sistema de origem;
- status;
- início e fim;
- duração;
- ambiente;
- erro ou causa de falha;
- identificadores de correlação;
- ativos de dados envolvidos.

### 6.3 Tarefa ou atividade

Unidade interna de execução pertencente a um processo.

### 6.4 Execução de tarefa

Instância de uma tarefa ou atividade, associada a uma execução de processo.

### 6.5 Ativo de dados

Objeto de dados observável, como:

- tabela;
- view;
- arquivo;
- dataset;
- stream;
- tópico;
- produto de dados;
- outro ativo governado.

### 6.6 Regra de qualidade

Definição reutilizável de uma condição esperada sobre um ativo, coluna, relacionamento ou conjunto de dados.

### 6.7 Execução de regra

Resultado da aplicação de uma regra de qualidade em um determinado contexto e instante.

### 6.8 Referência a Data Contract

Referência a um contrato de dados definido e monitorado por uma frente específica. Neste escopo, o contrato é tratado como contexto externo que pode ser associado ao ativo, à execução, a uma violação, a um alerta ou a um incidente. A definição, o versionamento e o monitoramento do contrato não são responsabilidades desta capacidade.

### 6.9 Evento de observabilidade

Registro normalizado que representa um fato relevante sobre execução, dado, qualidade, disponibilidade ou governança, incluindo violações de Data Contracts recebidas de uma fonte responsável quando aplicável.

### 6.10 Alerta

Condição detectada que exige atenção ou avaliação operacional.

### 6.11 Incidente

Registro operacional criado para tratamento de uma condição que requer acompanhamento formal.

### 6.12 Produto ou domínio de dados

Agrupamento lógico utilizado para governança, responsabilidade, priorização e indicadores.

---

## 7. Ownership e governança

A plataforma deve consumir e utilizar informações de ownership e governança definidas pelas políticas corporativas.

Este documento **não define quais tags são obrigatórias**, pois essa decisão pertence à política corporativa de governança de dados.

A capacidade deve, entretanto, permitir:

- associar ativos aos responsáveis definidos pelas políticas vigentes;
- identificar domínio, produto, criticidade e demais classificações quando disponíveis;
- identificar ativos que não estejam aderentes aos requisitos corporativos de governança;
- utilizar metadados de governança para roteamento de alertas, priorização e indicadores;
- manter rastreabilidade sobre a fonte do metadado utilizado.

Para qualquer ativo governado, deve ser possível determinar, conforme as políticas vigentes:

- responsável de negócio;
- responsável técnico ou de qualidade;
- domínio;
- criticidade;
- produto de dados, quando aplicável.

---

## 8. Observabilidade operacional

A plataforma deve centralizar sinais operacionais provenientes dos diferentes mecanismos de execução.

Deve permitir, no mínimo:

- status de execução;
- falha, sucesso, cancelamento e timeout;
- duração;
- início e término;
- recorrência de falhas;
- identificação de tarefas ou atividades com problema;
- erro técnico;
- dependências entre execuções quando disponíveis;
- correlação com ativo de dados produzido ou consumido;
- comparação histórica.

### 8.1 Indicadores operacionais mínimos

- quantidade de execuções;
- taxa de sucesso;
- taxa de falha;
- quantidade de cancelamentos;
- quantidade de timeouts;
- falhas por processo;
- falhas por tarefa;
- duração média;
- mediana de duração;
- P95 de duração;
- P99 de duração quando relevante;
- tendência de duração;
- reincidência de falhas;
- tempo desde a última execução bem-sucedida;
- cumprimento de SLA/SLO operacional.

---

## 9. Data Observability

Data Observability representa a capacidade de observar o comportamento do dado além do sucesso técnico do processamento.

Um pipeline pode executar com sucesso e ainda produzir um resultado inadequado para consumo.

A plataforma deve permitir monitorar sinais como:

- freshness;
- timeliness;
- volume;
- ausência inesperada de dados;
- mudanças estruturais ou schema drift, quando esse sinal estiver disponível;
- alteração de distribuição;
- anomalias;
- violações de Data Contracts recebidas da frente responsável;
- comportamento fora de baseline;
- qualidade baseada em regras explícitas.

Data Observability e Data Quality são conceitos relacionados, mas não idênticos.

**Data Quality** avalia aderência a expectativas ou regras definidas.

**Data Observability** amplia essa visão para sinais técnicos, temporais, estruturais e estatísticos que podem indicar degradação mesmo sem uma regra de negócio explicitamente violada.

### 9.1 Priorização

Nem todos os sinais listados nesta seção têm o mesmo grau de exigência para o estado-alvo desta iniciativa. Seguindo o mesmo critério já aplicado ao lineage na §19 — onde níveis 1 e 2 compõem o estado-alvo e níveis 3 e 4 são registrados apenas como direção futura —, os sinais de Data Observability são classificados da seguinte forma:

Freshness e volume compõem o **MVP**. Os dois dependem de metadados declarativos já previstos na taxonomia de tags (`vc_refresh_frequency`, `vc_freshness_sla`) e de contagens simples de registros, e por isso podem ser produzidos sem exigir detecção estatística.

Alteração de distribuição, anomalias e schema drift ficam classificados como **Fase 2**, condicionados à decisão sobre o escopo de detecção estatística de anomalia — ver a nota a seguir. Ausência inesperada de dados é tratada separadamente como capacidade de MVP, mas por um componente diferente (o avaliador temporal descrito no Cen2), não pela comparação estatística de distribuição.

Comportamento fora de baseline em sentido amplo — isto é, qualquer desvio que dependa de um modelo estatístico mais sofisticado do que comparação percentual contra uma janela histórica — é **Direcional**, sem prazo definido, e depende da maturidade dos mecanismos de detecção estatística que a organização vier a adotar ou construir.

> **Nota sobre detecção estatística de anomalia (decisão pendente D2).** A classificação de `VOLUME_ANOMALY`, `DISTRIBUTION_ANOMALY` e `SCHEMA_DRIFT` como MVP ou Fase 2 depende de uma decisão do usuário que ainda não foi tomada — ver a seção de decisões pendentes no relatório de alterações que acompanha este ajuste. A recomendação avaliada, e ainda não confirmada, é:
>
> **A CONFIRMAR:** manter a detecção estatística de anomalia **fora do MVP**, declarado explicitamente como tal em vez de deixado implícito, com anomalia de volume simples (variação percentual contra janela histórica) como primeiro incremento de Fase 2, e schema drift via comparação periódica de snapshots do `INFORMATION_SCHEMA`. Esse é o terreno em que uma solução de mercado especializada tende a superar uma construção interna por larga margem, e tratar esses sinais como já cobertos enfraqueceria a análise de construir × comprar.

---

## 10. Data Quality — conceitos e dimensões

### 10.1 Completude

Avalia se os dados necessários estão presentes.

Exemplos:

- coluna obrigatória não pode ser nula;
- percentual de preenchimento deve superar determinado limite.

### 10.2 Unicidade

Avalia se registros ou chaves que deveriam ser únicos não apresentam duplicidade indevida.

Exemplos:

- chave primária deve ser única;
- combinação de campos deve possuir cardinalidade esperada.

### 10.3 Validade

Avalia se o valor pertence ao domínio, formato, intervalo ou padrão esperado.

Exemplos:

- código deve pertencer a uma lista permitida;
- data não pode ser futura;
- valor deve respeitar expressão regular.

### 10.4 Consistência

Avalia se diferentes campos, registros ou datasets não se contradizem.

Exemplos:

- data final não pode ser anterior à data inicial;
- total deve ser coerente com a soma dos itens.

### 10.5 Integridade referencial

Avalia se referências entre entidades são válidas.

Exemplos:

- chave estrangeira deve existir na entidade de referência;
- identificador de cliente deve existir no cadastro mestre.

### 10.6 Acurácia

Avalia o grau de proximidade do dado em relação à realidade ou a uma fonte considerada confiável.

A acurácia normalmente exige uma fonte de referência, reconciliação ou validação externa.

### 10.7 Conformidade

Avalia aderência a padrões corporativos, regulatórios ou técnicos.

Exemplos:

- formato padronizado;
- códigos corporativos;
- regras regulatórias.

### 10.8 Freshness

Avalia quão recente é o dado disponível em relação à atualização esperada.

Exemplo:

- tabela deve possuir dados atualizados há no máximo 2 horas.

### 10.9 Timeliness

Avalia se o dado foi disponibilizado no momento em que deveria estar disponível.

Freshness e timeliness são relacionados, mas distintos.

Um dado pode estar atualizado, mas ter sido disponibilizado depois do horário acordado.

### 10.10 Volume

Avalia se a quantidade de dados se encontra dentro de limites ou comportamento esperado.

Exemplos:

- quantidade mínima de registros;
- variação máxima em relação ao período anterior.

### 10.11 Distribuição e anomalia

Avalia mudanças relevantes no comportamento estatístico dos dados.

Exemplos:

- mudança brusca de média;
- desvio na distribuição de categorias;
- alteração significativa de cardinalidade;
- presença de outliers acima do comportamento histórico.

### 10.12 Priorização

As onze dimensões acima não têm o mesmo grau de exigência para o estado-alvo desta iniciativa, e explicitar essa priorização evita que qualquer candidata pareça atender "um pouco de tudo" sem que a matriz comparativa consiga discriminar entre elas.

Completude, unicidade, validade, integridade referencial, freshness, timeliness e volume compõem o **MVP**. Essas sete dimensões correspondem diretamente às capacidades que o Metadata Collector / Rule Generator descrito no Cen2 já é capaz de derivar automaticamente a partir de constraints declaradas, tags e metadados estruturais do Unity Catalog (chave primária e estrangeira para unicidade e integridade referencial, `data_semantic_type` para validade semântica, `vc_refresh_frequency`/`vc_freshness_sla` para freshness e timeliness), complementadas por regras simples de contagem para volume.

Consistência e conformidade ficam classificadas como **Fase 2**. Consistência tipicamente exige regras que comparam múltiplos campos ou datasets entre si, o que normalmente demanda cadastro manual de regra em vez de derivação automática a partir de metadado; conformidade depende de catálogos corporativos de padrões regulatórios e técnicos que ainda não estão consolidados para esta iniciativa.

Distribuição e anomalia seguem a classificação definida na §9.1 para Data Observability, condicionada à decisão pendente D2 sobre o escopo de detecção estatística.

Acurácia é **Direcional**, sem prazo definido. Ela normalmente exige uma fonte de referência externa ou um processo de reconciliação que está fora do escopo desta capacidade — avaliar acurácia de forma confiável tende a depender de uma iniciativa própria de qualidade de dados mestres ou de reconciliação com sistemas de origem, e não apenas de um mecanismo de observabilidade.

---

## 11. Modelo conceitual de regras de qualidade

Cada regra de qualidade deve possuir metadados suficientes para explicar:

- o que está sendo validado;
- em qual ativo;
- em qual coluna ou conjunto de colunas, quando aplicável;
- qual dimensão de qualidade representa;
- qual lógica ou condição é avaliada;
- qual limite é aceitável;
- qual a severidade;
- se a falha é bloqueante;
- quem é responsável pela regra;
- seu estado no ciclo de vida;
- sua versão;
- quando entrou em vigor.

### 11.1 Ciclo de vida da regra

A plataforma deve permitir representar estados como:

- rascunho;
- ativa;
- suspensa;
- descontinuada.

Mudanças relevantes devem ser auditáveis.

### 11.2 Tipos de limiar

A plataforma deve suportar conceitualmente:

**Limiar absoluto**

Exemplo: mais de 0 registros inválidos.

**Limiar relativo**

Exemplo: mais de 1% de registros inválidos.

**Baseline histórico**

Exemplo: volume atual significativamente abaixo do comportamento histórico.

**Condição categórica**

Exemplo: presença de valor fora de domínio.

---

## 12. Resultados de qualidade

O resultado da execução de uma regra deve ser separado do estado do dado.

Uma execução pode produzir, conceitualmente:

- PASS;
- FAIL;
- WARNING;
- ERROR;
- NOT_EXECUTED;
- UNKNOWN.

### 12.1 PASS

A regra foi executada e os critérios foram atendidos.

### 12.2 FAIL

A regra foi executada corretamente e os dados violaram os critérios definidos.

### 12.3 WARNING

A regra identificou condição que requer atenção, mas que não representa falha formal conforme a política aplicável.

### 12.4 ERROR

A regra não pôde ser avaliada corretamente por erro técnico.

Um ERROR não deve ser automaticamente interpretado como evidência de dado inválido.

### 12.5 NOT_EXECUTED

A regra deveria ter sido considerada no contexto, mas não foi executada.

### 12.6 UNKNOWN

Não há informação suficiente para determinar o estado.

A plataforma deve preservar essa distinção em métricas, alertas e scorecards.

---

## 13. Métricas de Data Quality

### 13.1 Métricas por execução de regra

Devem estar disponíveis, quando aplicáveis:

- registros avaliados;
- registros aprovados;
- registros inválidos;
- percentual de registros inválidos;
- percentual de registros aprovados;
- status da regra;
- duração da avaliação;
- threshold aplicado;
- baseline utilizado;
- data/hora da execução.

### 13.2 Fórmulas básicas

Quando o conceito de registro for aplicável:

`fail_rate = failed_records / checked_records`

`pass_rate = passed_records / checked_records`

Quando `checked_records = 0`, a regra deve definir comportamento explícito para evitar interpretação incorreta.

### 13.3 Métricas por ativo

- quantidade de regras aplicáveis;
- quantidade de regras executadas;
- cobertura de execução;
- regras com PASS;
- regras com FAIL;
- regras com WARNING;
- regras com ERROR;
- regras não executadas;
- falhas bloqueantes;
- taxa de falha de regras;
- percentual de registros inválidos;
- freshness;
- tendência da qualidade;
- reincidência de falhas.

### 13.4 Métricas por domínio ou produto

- cobertura de regras;
- cobertura de ativos monitorados;
- ativos com falhas críticas;
- ativos sem resultado recente;
- falhas por dimensão de qualidade;
- evolução temporal;
- cumprimento de níveis mínimos de qualidade;
- indicadores agregados de confiabilidade.

---

## 14. Quality Score

O uso de um **Quality Score corporativo** deve permanecer como item a ser definido em etapa posterior.

A plataforma deve preservar os dados necessários para que diferentes metodologias de score possam ser calculadas sem reprocessar a origem.

### 14.1 Requisitos para futura metodologia

A metodologia deverá definir explicitamente:

- granularidade do score;
- período considerado;
- peso das regras;
- peso da severidade;
- comportamento para regras bloqueantes;
- tratamento de regras não executadas;
- tratamento de ERROR e UNKNOWN;
- agregação entre regras;
- agregação entre ativos;
- agregação por produto e domínio;
- score mínimo aceitável;
- tratamento de histórico e tendência.

### 14.2 Sugestão inicial para avaliação futura

Como ponto de partida, uma regra baseada em registros poderia possuir:

`rule_score = 1 - fail_rate`

O score de um ativo poderia ser calculado como média ponderada:

`asset_quality_score = SUM(rule_score * weight) / SUM(weight)`

O peso poderia considerar criticidade da regra, severidade ou relevância de negócio.

Entretanto, essa abordagem deve ser avaliada com cautela, pois médias podem mascarar falhas críticas. Uma futura metodologia deve considerar mecanismos como:

- teto de score na presença de falha bloqueante;
- penalização por regras não executadas;
- penalização diferenciada por severidade;
- separação entre score de qualidade e estado operacional.

Nenhuma fórmula de Quality Score é considerada mandatória neste escopo.

---

## 15. Integração com Data Contracts

Data Contracts são relevantes para Observabilidade e Data Quality como uma fonte externa de expectativas e violações, mas **a definição, governança, versionamento e monitoramento de mudanças dos contratos pertencem a uma frente específica e não fazem parte deste escopo**.

A capacidade de Observabilidade e Data Quality deve estar preparada para consumir e correlacionar informações produzidas por essa frente, sem assumir a responsabilidade de avaliar a conformidade do contrato por conta própria.

### 15.1 Limite de responsabilidade

Estão fora da responsabilidade desta capacidade:

- definição do conteúdo de um Data Contract;
- definição de políticas de compatibilidade;
- aprovação e versionamento de contratos;
- detecção de mudanças contratuais;
- monitoramento da evolução dos contratos;
- determinação primária de que uma mudança constitui violação contratual.

Essas responsabilidades devem permanecer na frente específica de Data Contracts.

### 15.2 Capacidades de integração

Quando a frente responsável disponibilizar informações de contrato ou de violação, a plataforma de Observabilidade e Data Quality deve poder:

- associar a referência do contrato ao ativo correspondente;
- receber ou consultar violações de contrato;
- correlacionar a violação com execução, ativo de dados e resultado de qualidade;
- utilizar a violação como contexto para alertas e incidentes, quando aplicável;
- preservar histórico suficiente para investigação e auditoria operacional;
- disponibilizar a informação para consultas e indicadores sem redefinir a lógica de Data Contracts.

A forma concreta de integração, o contrato de interface e os campos trocados deverão ser definidos posteriormente entre as frentes responsáveis.

### 15.3 Priorização

A integração descrita nesta seção é **Fase 2**. Ela depende de uma frente externa a esta iniciativa — a frente responsável por Data Contracts — disponibilizar referências e eventos de violação em um formato consumível, o que está fora do controle e do cronograma desta capacidade. O modelo conceitual e a arquitetura já preveem os pontos de extensão necessários para consumir Data Contracts quando essa frente estiver pronta (ver Solução Conceitual §15 e §14.4), mas a entrega do MVP desta capacidade não deve ficar bloqueada por essa dependência externa.

---

## 16. SLA, SLO e SLI

### 16.1 SLI — Service Level Indicator

Métrica observável utilizada para medir comportamento ou confiabilidade.

Exemplos:

- percentual de execuções bem-sucedidas;
- freshness medida;
- horário de disponibilização;
- duração;
- qualidade mínima;
- disponibilidade de um dataset.

### 16.2 SLO — Service Level Objective

Objetivo interno definido sobre um SLI.

Exemplo:

> 99% das atualizações mensais devem estar disponíveis até 06:00.

### 16.3 SLA — Service Level Agreement

Compromisso formal aplicável ao serviço ou produto quando houver acordo estabelecido.

### 16.4 Capacidades obrigatórias

A plataforma deve permitir:

- associar objetivos a ativos ou processos;
- medir cumprimento;
- calcular violações;
- manter histórico;
- alertar sobre violação ou risco de violação;
- diferenciar objetivo interno de compromisso formal.

### 16.5 Priorização

SLI e SLO de freshness compõem o **MVP** — eles se apoiam diretamente nas mesmas tags e no mesmo mecanismo de derivação automática de expectativa já previstos para o MVP de Data Observability na §9.1. SLOs sobre outras métricas (duração, qualidade mínima) e o tratamento formal de SLA como compromisso — distinto de objetivo interno — ficam classificados como **Fase 2**, na medida em que dependem de definição de compromissos formais entre áreas, o que é um processo organizacional e não apenas técnico.

---

## 17. Criticidade, severidade e prioridade

Devem ser tratados como conceitos diferentes.

### 17.1 Criticidade do ativo

Representa a relevância do ativo ou produto para o negócio.

### 17.2 Severidade da regra ou condição

Representa a gravidade potencial de uma violação específica.

### 17.3 Severidade do evento ou incidente

Representa a gravidade operacional resultante após considerar contexto, impacto, criticidade e condição detectada.

A política de cálculo ou mapeamento de severidade deve ser definida fora deste escopo, mas a plataforma deve suportar essa distinção.

---

## 18. Rastreabilidade e correlação fim a fim

A plataforma deve permitir correlacionar, quando tecnicamente disponível:

- processo;
- execução;
- tarefa;
- ativo consumido;
- ativo produzido;
- regra executada;
- resultado de qualidade;
- referência ou violação de Data Contract, quando disponível;
- alerta;
- incidente.

Deve ser possível responder perguntas como:

- qual execução produziu este dado;
- quais validações foram executadas;
- qual regra falhou;
- qual processo estava associado;
- qual incidente foi aberto;
- quais ativos upstream e downstream estão relacionados, conforme o lineage de datasets disponível;
- quem é responsável pelo tratamento.

Identificadores de correlação devem ser preservados quando disponíveis.

---

## 19. Lineage — níveis de maturidade

Lineage será tratado por níveis de maturidade. **Neste escopo, os níveis 1 e 2 compõem o estado-alvo da capacidade. Os níveis 3 e 4 são registrados apenas como direção de evolução futura e não devem ser considerados requisitos de entrega ou de aceite desta iniciativa.**

### 19.1 Nível 1 — Rastreabilidade de execução

Capacidade de identificar:

- processo executado;
- instante;
- resultado;
- ativos envolvidos;
- qualidade associada.

Este nível é considerado fundamental.

### 19.2 Nível 2 — Lineage de datasets

Capacidade de representar relações upstream e downstream entre ativos.

Exemplo conceitual:

`origem -> raw -> cleansed -> curated -> consumo`

Este nível deve fazer parte do estado-alvo.

Esse requisito é considerado atendido **por referência à fonte nativa de lineage**, quando ela existir — no ambiente atual, o lineage de tabelas do Unity Catalog (`system.access.table_lineage`) —, e não exige que a capacidade de Observabilidade mantenha um repositório corporativo próprio de relações de lineage. A Solução Conceitual (§16) e o Cen2 (seção "Lineage e rastreabilidade") já estabelecem esse princípio; ele é registrado aqui explicitamente porque o Escopo, isoladamente, poderia ser lido como exigindo um grafo de lineage construído e mantido pela própria capacidade, o que não é a intenção.

Essa forma de atendimento tem uma lacuna conhecida e aceita: o lineage nativo do Unity Catalog cobre relações entre tabelas dentro do Databricks, mas não cobre lineage cross-technology — isto é, a cadeia completa origem → ADF → Databricks → consumo em Power BI não fica inteiramente representada apenas pelo lineage nativo do Unity Catalog. A parte dessa cadeia que atravessa tecnologias diferentes do Databricks é coberta pela rastreabilidade operacional entre processo, execução e ativo (Nível 1), não por um grafo de lineage de datasets unificado. Essa lacuna é registrada aqui como conhecida e aceita, e não deve ser tratada como uma reprovação da alternativa interna nem de nenhuma outra candidata: nenhuma delas está sendo avaliada para entregar lineage cross-technology completo como requisito de aceite desta iniciativa.

### 19.3 Nível 3 — Análise de impacto

Capacidade de, a partir de um ativo ou evento, identificar potenciais consumidores e ativos downstream afetados.

Exemplo:

> Se a tabela X possui uma falha crítica, quais datasets, produtos ou consumidores podem ser impactados?

**Este nível não faz parte do escopo desta iniciativa e não deve ser utilizado como requisito para avaliação, implementação ou aceite das soluções atuais.** Ele é registrado exclusivamente como objetivo de evolução futura, a ser tratado em iniciativa ou escopo próprio quando houver maturidade de lineage, fontes confiáveis de dependência e necessidade de negócio claramente estabelecida.

### 19.4 Nível 4 — Lineage em nível de coluna

Capacidade de rastrear origem e transformação de colunas específicas.

Este nível também é uma capacidade futura, aplicável apenas quando houver valor de negócio e viabilidade técnica, e não integra os requisitos de entrega ou aceite desta iniciativa.

Para fins deste documento, somente os níveis 1 e 2 são requisitos do estado-alvo atual.

---

## 20. Alertas

A plataforma deve permitir detectar condições que exijam atenção.

Alertas podem ser originados por:

- falha operacional;
- timeout;
- cancelamento;
- quebra de regra de qualidade;
- falha bloqueante;
- freshness;
- atraso;
- quebra de SLA/SLO;
- volume anômalo;
- schema drift;
- violações de Data Contracts recebidas da frente responsável;
- ausência de execução;
- recorrência de erro;
- condição anômala relevante.

### 20.1 Requisitos de alertamento

A plataforma deve suportar:

- severidade;
- prioridade;
- contexto;
- owner;
- domínio;
- ativo afetado;
- origem;
- data/hora;
- evidências;
- deduplicação;
- agrupamento;
- supressão;
- cooldown;
- escalonamento;
- reconhecimento;
- atualização;
- resolução.

---

## 21. Ciclo de vida de alertas e incidentes

O ciclo operacional deve contemplar:

`Detecção -> Classificação -> Correlação -> Deduplicação -> Priorização -> Notificação -> Incidente -> Acknowledgment -> Tratamento -> Recuperação -> Encerramento`

### 21.1 Detecção

Identificação de uma condição que viola uma expectativa.

### 21.2 Classificação

Determinação do tipo do evento e do contexto associado.

### 21.3 Correlação

Associação com processo, ativo, regra, execução, incidente já existente e, quando disponível, referência ou violação de Data Contract.

### 21.4 Deduplicação

Prevenção da criação de múltiplos alertas ou incidentes equivalentes para a mesma causa.

### 21.5 Priorização

Determinação de impacto e prioridade conforme políticas corporativas.

### 21.6 Notificação

Envio da informação aos responsáveis ou canais adequados.

### 21.7 Criação ou atualização de incidente

Condições que exijam tratamento formal devem poder criar ou atualizar um incidente na plataforma corporativa de ITSM.

No contexto atual, ServiceNow é a ferramenta corporativa utilizada para esse processo.

### 21.8 Acknowledgment

Registro de que o incidente foi recebido ou assumido por um responsável.

### 21.9 Tratamento

Acompanhamento das ações tomadas.

### 21.10 Recuperação

Detecção de que a condição deixou de existir.

### 21.11 Encerramento

Atualização do estado operacional e encerramento do incidente conforme processo corporativo.

### 21.12 Automação corporativa

A plataforma deve disponibilizar informação suficiente para que mecanismos corporativos de automação ou orquestração avaliem condições e acionem processos de incidente.

No contexto atual, o Control-M pode ser utilizado como mecanismo de execução periódica para avaliar eventos ou estados de alerta e integrar com o ServiceNow.

Essa possibilidade é um exemplo aderente ao ambiente atual, não uma obrigação arquitetural deste escopo.

### 21.13 Priorização

Detecção, classificação, correlação, deduplicação, priorização, notificação e a criação/atualização de incidente pelo caminho do MVP (§21.7, apoiado em Control-M e ServiceNow conforme §21.12) compõem o **MVP**. Esse conjunto de etapas do ciclo não depende de integração direta com o ITSM — o MVP consegue informar a uma equipe de suporte que existem inconsistências ativas sob sua responsabilidade sem precisar ler ou escrever o estado do chamado.

Acknowledgment (§21.8) e a sincronização de estado necessária para tratamento, recuperação e encerramento observados a partir do próprio ITSM (§21.9 a §21.11, na medida em que dependam de consultar o estado do chamado, e não apenas do sinal da Observabilidade) são **Fase 2**, porque dependem de integração direta com o sistema ITSM — uma evolução explicitamente descrita como futura na Solução Conceitual §22, e não uma capacidade do MVP baseado em Control-M.

---

## 22. Estado e deduplicação de incidentes

Para evitar ruído operacional, a capacidade deve suportar conceitos como:

- incidente novo;
- incidente já existente;
- evento recorrente;
- evento correlacionado;
- incidente em tratamento;
- condição recuperada;
- incidente encerrado;
- reabertura.

Deve ser possível evitar cenários como:

- abrir vários chamados para a mesma falha recorrente;
- abrir um chamado para cada registro inválido;
- duplicar incidentes por diferentes camadas técnicas quando a causa é única.

A estratégia concreta de deduplicação pertence à Solução Conceitual.

---

## 23. Métricas e scorecards

### 23.1 Operação

- taxa de sucesso;
- taxa de falha;
- falhas por processo;
- falhas por tarefa;
- duração média;
- P95;
- P99 quando relevante;
- cumprimento de SLA/SLO;
- freshness;
- reincidência.

### 23.2 Qualidade

- regras aplicáveis;
- regras executadas;
- cobertura;
- regras com falha;
- falhas bloqueantes;
- percentual de registros inválidos;
- falhas por dimensão;
- evolução temporal;
- Quality Score, quando definido.

### 23.3 Governança

- ativos sem metadados obrigatórios conforme política;
- ativos sem owner;
- ativos sem steward/responsável equivalente;
- cobertura por domínio;
- cobertura de regras de qualidade.

### 23.4 Incidentes

- alertas gerados;
- alertas deduplicados;
- incidentes abertos;
- incidentes por severidade;
- incidentes por domínio;
- tempo para acknowledgment;
- tempo para resolução;
- recorrência;
- incidentes reabertos.

### 23.5 Lineage

Conforme maturidade disponível:

- cobertura de lineage;
- ativos sem upstream identificado;
- ativos sem downstream identificado;
- ativos críticos com lineage incompleto;

### 23.6 Priorização

As métricas de operação (§23.1), qualidade (§23.2, exceto Quality Score, que permanece em aberto conforme §14) e governança (§23.3) compõem o **MVP**, porque derivam diretamente de entidades e eventos que já são MVP em outras seções deste documento (execuções, resultados de qualidade, metadados de governança).

As métricas de incidentes que dependem do ciclo ITSM (§23.4 — tempo para acknowledgment, tempo para resolução, incidentes reabertos) são **Fase 2**, pela mesma razão que a Acknowledgment é Fase 2 na §21.13: dependem de integração direta com o ITSM. As métricas de incidentes que não dependem desse ciclo (alertas gerados, alertas deduplicados, incidentes por severidade e por domínio) podem ser produzidas no MVP a partir do próprio estado de condições da Observabilidade.

As métricas de lineage (§23.5) seguem a maturidade de lineage definida na §19: cobertura de lineage Nível 1 e 2 é MVP; métricas que dependeriam de níveis 3 ou 4 (por exemplo, cobertura de análise de impacto) são Direcionais, coerente com o fato de esses níveis não serem requisito de aceite desta iniciativa.

---

## 24. Histórico, auditoria e replay

A plataforma deve manter histórico suficiente para:

- análise de tendência;
- auditoria;
- investigação de incidentes;
- comparação entre períodos;
- avaliação de SLA/SLO;
- evolução da qualidade;
- análise histórica de violações de Data Contracts recebidas, quando aplicável;
- rastreabilidade de alterações.

Quando tecnicamente aplicável, deve ser possível reprocessar ou reconstruir estados a partir de eventos históricos ou dados brutos preservados.

A política concreta de retenção será definida na Solução Conceitual ou em políticas corporativas específicas.

---

## 25. Segurança, privacidade e LGPD

A capacidade deve seguir as políticas corporativas de segurança e proteção de dados.

Princípios mínimos:

- evitar armazenamento desnecessário de dados de negócio em logs;
- evitar exposição de registros inválidos completos em alertas;
- mascarar ou sanitizar mensagens de erro quando necessário;
- não armazenar credenciais em eventos;
- aplicar menor privilégio;
- registrar acessos e alterações relevantes;
- permitir classificação e proteção conforme LGPD;
- preservar apenas metadados necessários para observabilidade sempre que possível.

---

## 26. Requisitos não funcionais

### 26.1 Extensibilidade

Deve ser possível incorporar novas fontes sem redefinir o modelo conceitual central.

### 26.2 Portabilidade

O modelo de eventos e entidades deve evitar dependência conceitual desnecessária de um único fornecedor.

O ambiente atual é Azure, mas futuras fontes de outras nuvens ou plataformas devem poder ser incorporadas.

### 26.3 Latência

Eventos críticos devem ser disponibilizados em tempo compatível com operação e tratamento de incidentes.

Os limites concretos devem ser definidos conforme classe de evento e criticidade.

### 26.4 Disponibilidade

A capacidade de observabilidade deve possuir disponibilidade compatível com os processos que monitora.

### 26.5 Escalabilidade

A arquitetura futura deve suportar crescimento de:

- fontes;
- eventos;
- ativos;
- regras;
- consumidores;
- histórico.

### 26.6 Auditabilidade

Mudanças em regras de qualidade, severidades e configurações relevantes da capacidade de observabilidade devem ser rastreáveis.

### 26.7 Interoperabilidade

A plataforma deve permitir integração com:

- orquestradores;
- catálogos;
- mecanismos de Data Quality;
- ferramentas de visualização;
- sistemas de notificação;
- plataformas de ITSM;
- mecanismos corporativos de automação.

### 26.8 Resiliência

Falha temporária em um componente de observabilidade não deve, sempre que possível, implicar perda irreversível do evento original.

### 26.9 Reprocessamento

Quando aplicável, deve existir capacidade de recompor estados ou reprocessar eventos após correção de falha técnica.

### 26.10 Manutenibilidade

A solução futura deve minimizar código proprietário desnecessário e favorecer contratos, padrões e componentes reutilizáveis.

---

## 27. Critérios gerais de sucesso

O estado-alvo será considerado funcionalmente bem-sucedido quando permitir, de forma consistente:

1. consultar execuções de diferentes tecnologias em uma visão comum;
2. identificar rapidamente falhas e contexto;
3. relacionar execução e ativo de dados;
4. avaliar Data Quality de forma padronizada;
5. distinguir falha de dado de erro técnico de validação;
6. medir freshness e níveis de serviço;
7. correlacionar violações de Data Contracts recebidas da frente responsável, quando disponíveis;
8. consultar ownership e criticidade conforme governança;
9. relacionar alertas e incidentes;
10. reduzir duplicação de alertas;
11. produzir histórico e tendências;
12. atender aos níveis 1 e 2 de lineage definidos neste escopo — o Nível 2 satisfeito por referência à fonte nativa de lineage disponível, sem exigir repositório próprio, e com a lacuna de lineage cross-technology explicitamente registrada como conhecida e aceita na §19.2 —, preservando possibilidade de evolução futura para os níveis 3 e 4;
13. incorporar novas fontes sem redefinir o núcleo conceitual;
14. suportar indicadores gerenciais de confiabilidade.

Os critérios acima devem ser lidos em conjunto com a priorização MVP / Fase 2 / Direcional estabelecida nas seções correspondentes deste documento (§9.1, §10.12, §15.3, §16.5, §21.13, §23.6): um critério de sucesso não é uma exigência para o MVP quando a capacidade subjacente está classificada como Fase 2 ou Direcional.

---

## 28. Fora de escopo

Não fazem parte deste documento:

- seleção de ferramenta;
- decisão entre arquiteturas candidatas;
- definição de produto ou fornecedor;
- desenho físico da solução;
- schemas físicos de tabelas;
- nomes de tópicos, filas ou APIs;
- definição concreta das tags corporativas;
- política corporativa de tags;
- infraestrutura como código;
- implementação de dashboards;
- configuração específica de ServiceNow;
- configuração específica de Control-M;
- definição, governança, versionamento e política de compatibilidade de Data Contracts;
- monitoramento de mudanças e detecção primária de violações de Data Contracts, que pertencem à frente específica responsável;
- metodologia definitiva do Quality Score;
- roadmap ou priorização de MVP;
- FinOps e observabilidade de custos da plataforma.

---

## 29. Relação com a Solução Conceitual

Este documento deve ser complementado por um documento separado de **Solução Conceitual**.

A Solução Conceitual deverá transformar os requisitos deste escopo em padrões compartilhados entre as arquiteturas candidatas, podendo definir:

- metadados canônicos;
- taxonomia de eventos;
- estados;
- convenções;
- regras de correlação;
- tags consumidas da governança;
- modelo de dados conceitual;
- estruturas de Data Quality;
- padrões de integração com a frente responsável por Data Contracts;
- estrutura de alertas;
- política de severidade;
- regras de deduplicação;
- estratégia de retenção;
- padrões de integração;
- critérios técnicos para lineage.

Somente após a consolidação do Escopo e da Solução Conceitual as arquiteturas candidatas devem ser avaliadas comparativamente.

---

## 30. Itens em aberto

### 30.1 Quality Score — Por definir

A metodologia corporativa de **Quality Score permanece em aberto** e deverá ser definida em etapa posterior.

A definição futura deverá estabelecer, no mínimo:

- granularidade do score;
- janela temporal considerada;
- pesos por regra e/ou dimensão;
- influência da severidade e de regras bloqueantes;
- tratamento de `ERROR`, `UNKNOWN` e regras não executadas;
- critérios de agregação entre regras, ativos, produtos e domínios;
- limites mínimos aceitáveis;
- comportamento do score diante de falhas críticas;
- forma de utilização do score em indicadores e scorecards.

A sugestão apresentada na seção 14 deve ser tratada apenas como referência inicial para discussão e **não representa uma definição aprovada**.
