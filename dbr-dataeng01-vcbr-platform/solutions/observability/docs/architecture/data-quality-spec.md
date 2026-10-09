---
id: SPEC-observabilidade-data-quality
companions:
  - architecture.md
  - data-contracts.md
  - metrics-and-acceptance.md
  - delivery-plan.md
sources:
  - ../../../doc/Observabilidade e Data Quality - Desenho Técnico de Referência.md
  - ../../../doc/Observabilidade e Data Quality - Discussões técnicas.md
  - ../../../doc/Observabilidade e Data Quality - Solução Conceitual Consolidada.md
---

> **Contrato canônico.** Este SPEC e os arquivos em `companions:` formam o contrato completo e validado do MVP. Os documentos em `sources:` permanecem apenas para rastreabilidade.

# Data Quality MVP

## Why

A VCBR precisa detectar, antes dos consumidores, quando processos não executam, cargas falham ou atrasam e dados chegam incompletos, anômalos ou incompatíveis. Os sinais hoje estão distribuídos entre ADF, Databricks, Unity Catalog, Power BI e plataformas corporativas sob diferentes responsáveis. O MVP deve integrar esses sinais, explicitar a cobertura possível e produzir evidências de valor, custo e esforço para decidir entre ampliar a solução interna ou adquirir uma plataforma especializada.

## Capabilities

- **CAP-1**
  - **intent:** Inventariar os dez ativos críticos do MVP, seus processos, domínio, criticidade, responsáveis, periodicidade e consumidores.
  - **success:** Cada ativo possui identidade estável e ficha mínima aprovada com processo produtor, expectativa de atualização e responsável.

- **CAP-2**
  - **intent:** Coletar os sinais de execução `PipelineRun`, `ActivityRun` e `TriggerRun` dos ADFs corporativos da VCBR.
  - **success:** As três categorias chegam completas ao histórico e uma falha controlada é detectada e notificável.

- **CAP-3**
  - **intent:** Coletar definições e execuções Databricks somente dos workspaces pertencentes à VC.
  - **success:** Inclusão ou remoção de workspace na view corporativa altera o escopo sem mudança de código e sem apagar histórico.

- **CAP-4**
  - **intent:** Manter histórico integrado de execução, saúde, qualidade, lineage, eventos, notificações e incidentes.
  - **success:** Reprocessamentos são idempotentes, preservam identificadores e rastreabilidade entre raw, cleansed e curated e não duplicam registros.

- **CAP-5**
  - **intent:** Correlacionar execuções ADF e Databricks para formar um contexto operacional único.
  - **success:** Pelo menos 95% das execuções elegíveis iniciadas pelo ADF são correlacionadas, e os casos restantes recebem estado diagnóstico explícito.

- **CAP-6**
  - **intent:** Avaliar ausência e atraso de execução, freshness técnica e de negócio e evolução do volume físico dos ativos monitorados.
  - **success:** Cada ativo exibe expectativa, observação, atraso e evidência de volume; contagem generalizada de linhas não é necessária.

- **CAP-7**
  - **intent:** Detectar mudanças de schema e avaliar regras mínimas de qualidade com um contrato de resultado comum.
  - **success:** Erro técnico é distinguido de reprovação do dado, reingestão não duplica resultados e registros inválidos permanecem fora das Observability Tables.

- **CAP-8**
  - **intent:** Registrar eventos, decidir elegibilidade de notificação, suprimir duplicidades e agrupar evidências do mesmo incidente.
  - **success:** Uma falha correlacionada ADF–Databricks gera um incidente e uma notificação principal, mantendo todas as evidências para diagnóstico.

- **CAP-9**
  - **intent:** Oferecer visões operacional e executiva da saúde dos processos e dados.
  - **success:** Os dashboards atualizam em até 60 minutos, permitem drill-down de ADF até task Databricks e não ocultam condições críticas em médias.

- **CAP-10**
  - **intent:** Representar lineage federado entre origens SAP/BW/Datasphere, Databricks e consumidores Power BI.
  - **success:** Relações são idempotentes e auditáveis, exibem o tipo de evidência e nunca apresentam cobertura declarada ou parcial como nativa ou completa.

- **CAP-11**
  - **intent:** Gerenciar alertas ADF, Databricks e Data Quality como configuração YAML versionada.
  - **success:** Um único pipeline valida o conjunto e publica o mesmo commit aprovado no Azure Monitor e na Databricks Git Folder, com operações idempotentes e detecção de drift.

- **CAP-12**
  - **intent:** Monitorar a própria solução e medir custo, latência, cobertura e esforço operacional.
  - **success:** O encerramento do MVP apresenta métricas reais suficientes para recomendar evolução interna ou avaliação de plataforma especializada.

- **CAP-13**
  - **intent:** Demonstrar observabilidade MLOps básica para até três workloads somente se o núcleo estiver estável.
  - **success:** Inventário e estado operacional mínimo são demonstrados sem atrasar entregas P0 e P1.

## Constraints

- MVP restrito à VCBR, dez ativos críticos, 90 dias e capacidade de 1,5 FTE; o caminho crítico operacional prevalece sobre itens opcionais.
- O escopo Databricks deve ser resolvido pela view corporativa `corp_curated.vw_vc_workspaces`, com ambiente `dev_it` ou `prd_it` determinado por biblioteca corporativa.
- As tabelas devem usar `dev_governance` ou `prd_governance` e os schemas `corp_raw`, `corp_cleansed` e `corp_curated`, sem nomes de ambiente fixados no código ou em YAML.
- O Log Analytics recebe somente telemetria ADF e mantém retenção operacional de 30 dias; o histórico oficial permanece em Delta por dois anos.
- Falhas explícitas ADF seguem o caminho rápido Azure Monitor → Logic App → Teams; falhas exclusivamente Databricks seguem o ciclo periódico.
- A coleta periódica deve usar watermark, janela de sobreposição, `MERGE` idempotente e um único workflow para ingestão, freshness, correlação, qualidade e alertas.
- `DESCRIBE DETAIL` é o padrão para freshness técnica e volume físico; `DESCRIBE HISTORY` é exceção sob demanda.
- `sizeInBytes` e `numFiles` não podem ser apresentados como quantidade de registros; linhas só serão usadas quando já produzidas pela execução ou por regra de qualidade.
- Lineage executa uma ou duas vezes ao dia, separado do ciclo operacional, e sua falha não bloqueia alertas e indicadores operacionais.
- SAP/BW/Datasphere têm cobertura declarada; Power BI limita-se aos workspaces e APIs autorizados; Control-M não é fonte do MVP.
- Nenhuma linha de negócio, segredo, token, parâmetro de conexão ou valor sensível reprovado pode ser persistido em logs ou Observability Tables.
- Teams é o canal inicial; as regras funcionais ficam em YAML, os segredos permanecem fora do repositório e a integração com a Logic App usa JSON.
- O Data Health Score é experimental: ausência de cobertura não equivale a zero e uma média não pode ocultar falha crítica.

## Non-goals

- Cobertura global de VCNA, Itacamba, VCEAA ou de todo o tenant Power BI.
- Alterar a governança ou substituir a monitoração de SAP, Power BI ou Control-M.
- Integrar automaticamente ServiceNow no MVP.
- Executar streaming, detecção multivariada, causa raiz automática ou observabilidade MLOps avançada.
- Copiar registros inválidos ou dados de negócio para as Observability Tables.
- Implantar ou selecionar uma plataforma corporativa multicloud durante o MVP.

## Success signal

Em até 90 dias, dez ativos VCBR possuem responsabilidade e expectativa formalizadas; falhas, ausências, atrasos, anomalias físicas e violações de qualidade são detectadas e investigáveis em uma visão correlacionada; alertas ADF chegam sem depender do ciclo periódico; e custo, cobertura, latência e esforço reais sustentam uma decisão explícita sobre a próxima etapa.

## Assumptions

- A view corporativa de workspaces e as bibliotecas de resolução de ambiente estarão disponíveis nos ambientes do MVP.
- O benchmark de 181 tabelas em 41,33 segundos com compute ativo é suficiente para reservar inicialmente um minuto por ciclo para `DESCRIBE DETAIL`; o piloto recalibrará a medição incluindo escrita.
- Operações de manutenção separadas são raras nos ativos iniciais, tornando `lastModified` uma aproximação aceitável de freshness técnica.
- Data Owners e Data Stewards participarão da definição de criticidade, calendário, SLA e regras mínimas.

## Open Questions

- A cadência operacional será fixa em 30 minutos ou configurável entre 30 e 60 minutos?
- Quais nomes físicos serão adotados para eliminar as divergências atuais nas tabelas de Data Quality, mudança de schema e lineage?
- Como será cadastrado e resolvido o time de suporte nos níveis domínio, produto, processo, ativo e regra?
- As Scanner/Admin APIs do Power BI estarão autorizadas e o `lineage-syncer` será aprovado como mecanismo oficial da PoC?
- Quais metas definitivas de SLA, qualidade e baseline serão aprovadas após a coleta inicial?
- A amostra de MLOps cabe no horizonte de 90 dias sem comprometer o núcleo operacional?
