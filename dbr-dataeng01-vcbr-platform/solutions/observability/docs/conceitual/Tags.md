# Tags

Escrito para: governança de dados e quem cadastra tags no Unity Catalog. Documento atualizado em 2026-10-06.

Este documento é a referência única das tags: lista as tags de tabela, o uso de cada uma na solução, os valores aceitos e as divergências ainda abertas.

## 1. Tags de tabela

Todas as tags de tabela:

| Tag | Obrigatória | Descrição | Exemplos de valores |
| --- | --- | --- | --- |
| vc_data_classification | Sim | Nível de confidencialidade do dado | public, internal, confidential, restricted |
| vc_contains_pii | Sim | Indica presença de dados pessoais | true, false |
| vc_privacy_category | Sim | Enquadramento conforme LGPD / privacidade | dados_pessoais, dados_sensiveis, nao_aplicavel |
| vc_data_retention | Sim | Prazo de retenção do dado | 5y, 7y, 10y |
| vc_business_unit | Sim | Unidade de negócio responsável | logistica_transporte, logistica_pcp, logistica_insumos, logistica_operacoes, gente, controladoria, suprimentos, industrial_exel_oper_proc, industrial_qualidade, industrial_manutencao, industrial_mineracao, industrial_gestao, industrial_seguranca, engemix, comercial_vendas, comercial_pricing, comercial_vloz |
| vc_company | Sim | Empresa cujos dados constam na tabela | VCBR, VCNE, VCEA, VCBO, GLOBAL, NONE |
| vc_source_system | Sim | Nome do sistema de origem | ds, bw, sql, postgres, api, flat_file, databricks |
| vc_source_object | Sim | Objeto que originou o dado (tabela, view, CDS, transação, API ou arquivo), campo livre digitável | R_GV_ZVC_CDS_SD_0026_CHKIN, AzureCostManagement, API_Orders |
| vc_criticality | Sim | Nível de criticidade operacional do ativo | low, medium, high, critical |
| vc_refresh_frequency | Sim | Frequência de atualização do dado | realtime, 5min, 1h, 1d, 1w, 1m |
| vc_freshness_sla | Sim | Tolerância de carga do dado operacional | realtime, 5min, 1h, 1d, 1w, 1m |
| vc_data_quality_tier | Sim | Maturidade / nível de qualidade do dado | certified, validated, experimental |
| vc_lifecycle_status | Sim | Estágio no ciclo de vida do ativo | active, deprecated, experimental, archived |



### vc_source_system
- **Obrigatória**: sim.
- **Descrição**: sistema de origem do dado.
- **Valores**: `ds`, `bw`, `sql`, `postgres`, `api`, `flat_file`, `databricks`.
- **Uso no código**: o `custom_lineage_reconciler` a lê junto com `vc_source_object` para criar o objeto externo de origem no Unity Catalog. O `asset_observability` só a grava nas tabelas de destino da solução, não a usa para avaliar ativos.

### vc_source_object
- **Obrigatória**: sim.
- **Descrição**: objeto que originou o dado (tabela, view, CDS, transação, API ou arquivo), no formato `<schema>.<objeto>`.
- **Valores**: texto livre. Exemplos: `3_INTEGRACAO.R_GV_ZVC_CDS_SD_0026_CHKIN`, `AzureCostManagement`, `API_Orders`.
- **Uso no código**: o reconciliador de lineage usa o trecho depois do último ponto como nome do objeto e o trecho antes como schema. Sem ponto, o schema vem de `--default-source-schema`.

### vc_criticality
- **Obrigatória**: sim.
- **Descrição**: criticidade operacional do ativo.
- **Valores**: `low`, `medium`, `high`, `critical`.
- **Uso no código**: define a severidade do incidente. `critical` e `high` viram `HIGH`, `medium` vira `MEDIUM`, `low` vira `LOW`. Valor desconhecido vira `MEDIUM`.

### vc_refresh_frequency
- **Obrigatória**: sim.
- **Descrição**: frequência de atualização do dado. Gera uma expectativa de freshness.
- **Valores**: `realtime`, `5min`, `1h`, `1d`, `1w`, `1m`.
- **Formato**: `<número><unidade>`, com unidade `min`, `h`, `d`, `w`, `m` ou `y`. `m` é mês (30 dias) e `y` é ano (365 dias), conforme o código. `1m` não significa um minuto.
- **Uso no código**: `asset_observability` avalia freshness e volume de toda tabela com frequência válida, qualquer que seja ela. `realtime` não tem prazo calculável: a tabela fica sem avaliação e o log registra o erro. Ver o [guia do asset_observability](../guias/asset_observability.md).

### vc_freshness_sla
- **Obrigatória**: sim. Existe na taxonomia corporativa (Pilar 05).
- **Descrição**: prazo máximo para o dado estar disponível. Não é o mesmo que a frequência: a frequência diz de quanto em quanto tempo o dado é atualizado, e o SLA diz quanto atraso o negócio aceita.
- **Valores**: `realtime`, `5min`, `1h`, `1d`, `1w`, `1m`.
- **Uso no código**: se existir, substitui `vc_refresh_frequency` como prazo de freshness. Só vale para tabela que também tem `vc_refresh_frequency`.

### vc_data_classification
- **Obrigatória**: sim.
- **Valores**: `public`, `internal`, `confidential`, `restricted`.
- **Uso no código**: não consumida pela solução.

### vc_contains_pii
- **Obrigatória**: sim.
- **Valores**: `true`, `false`.
- **Uso no código**: não consumida pela solução.

### vc_privacy_category
- **Obrigatória**: sim.
- **Descrição**: enquadramento conforme LGPD e privacidade.
- **Valores**: `dados_pessoais`, `dados_sensiveis`, `nao_aplicavel`.
- **Uso no código**: não consumida pela solução.

### vc_data_retention
- **Obrigatória**: sim.
- **Descrição**: prazo de retenção do dado.
- **Valores**: `5y`, `7y`, `10y`.
- **Uso no código**: não consumida pela solução.

### vc_business_unit
- **Obrigatória**: sim.
- **Descrição**: unidade de negócio responsável pelo ativo.
- **Valores**: `logistica_transporte`, `logistica_pcp`, `logistica_insumos`, `logistica_operacoes`, `gente`, `controladoria`, `suprimentos`, `industrial_exel_oper_proc`, `industrial_qualidade`, `industrial_manutencao`, `industrial_mineracao`, `industrial_gestao`, `industrial_seguranca`, `engemix`, `comercial_vendas`, `comercial_pricing`, `comercial_vloz`.
- **Uso no código**: não consumida pela solução.

### vc_company
- **Obrigatória**: sim.
- **Descrição**: empresa cujos dados aparecem na tabela.
- **Valores**: `VCBR`, `VCNE`, `VCEA`, `VCBO`, `GLOBAL`, `NONE`.
- **Uso no código**: não consumida pela solução.

### vc_lifecycle_status
- **Obrigatória**: sim.
- **Valores**: `active`, `deprecated`, `experimental`, `archived`.
- **Uso no código**: não consumida pela solução.

### observability_enabled
- **Obrigatória**: sim.
- **Descrição**: indica se o ativo participa das automações de observabilidade e DQ. Permite distinguir um ativo que optou por não participar de um que ainda não foi tagueado.
- **Valores**: `true`, `false`.
- **Uso no código**: não implementada. Hoje a cobertura é decidida pela presença das tags de origem e de frequência.

## 2. Tags que o código usa e não estão na tabela da seção 1

Essas tags aparecem no código, mas não estão na lista de tags de tabela. Ou a lista é atualizada, ou o código deixa de usá-las. Veja a seção 4.

### vc_data_domain
- **Descrição**: domínio de negócio do ativo. Classifica o incidente nos relatórios; não define o destino do alerta, que vem das regras de alerta do hub.
- **Valores**: `finance`, `logistics`, `supply_chain`, `manufacturing`, `hr`. O conjunto final depende da decisão de governança (seção 4).
- **Uso no código**: `asset_observability` e `schema_observability` leem a tag, ou a herdam do schema. Sem a tag, o ativo cai no domínio `DEFAULT`.

### vc_medallion_layer
- **Descrição**: camada do modelo medalhão. Ela é derivada do schema de destino, então não é preenchida à mão.
- **Uso no código**: `asset_observability` a grava nas tabelas de destino.

## 3. Colunas

### vc_source_column
- **Descrição**: coluna de origem de uma coluna do Unity Catalog. Permite montar o mapeamento de colunas do lineage externo.
- **Status**: o reconciliador (`lineage/reconciler.py`) já lê `vc_source_column`. A tag ainda precisa existir na taxonomia corporativa para que o mapeamento funcione. A spec 23 registra isso.

### vc_data_semantic_type
- **Descrição**: significado funcional da coluna, para associar validações padronizadas. Exemplos: `cpf`, `cnpj`, `email`, `birth_date`, `phone`.
- **Status**: proposta. Não há código que a use, **não implementado**.

## 3b. Tags de schema (herdadas pelas tabelas)

Estas tags ficam no nível do schema. As tabelas as herdam, e não repetem o valor.

### vc_data_owner
- **Nível**: schema, herdada pelas tabelas.
- **Descrição**: e-mail corporativo do responsável de negócio pela informação.
- **Valores**: e-mail corporativo.
- **Obrigatória**: não confirmada. Depende da política de governança.
- **Uso no código**: não consumida pela solução. Os indicadores de governança (por exemplo, ativo sem owner) dependem dela, mas ainda não estão implementados.

### vc_data_steward
- **Nível**: schema, herdada pelas tabelas.
- **Descrição**: e-mail corporativo do responsável técnico pela curadoria e qualidade.
- **Valores**: e-mail corporativo.
- **Obrigatória**: não confirmada. Depende da política de governança.
- **Uso no código**: não consumida pela solução.


## 4. Divergências em aberto

Estas questões dependem de decisão de governança e não foram resolvidas neste documento.

- **Tags usadas pelo código e ausentes da seção 1**: `vc_data_domain` e `vc_medallion_layer`. Decidir se entram na taxonomia e com quais valores.


## 5. Decisões de modelagem

- Regras de qualidade não viram tag. Limiares, severidade e condições ficam na configuração da DQ.
- Constraints do Unity Catalog, como PK, FK, UNIQUE e NOT NULL, são lidas do catálogo e não duplicadas como tag.
