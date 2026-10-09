# 50 - Scripts PowerShell - Diagnostic Settings

| Item | Valor |
|---|---|
| Tipo de componente | Scripts PowerShell de operação, execução esporádica |
| Repositório | `dbr-dataeng01-vcbr-platform` (este repositório). |
| Arquivo desta spec | `solutions/observability/docs/Especs/50-Scripts-Diagnostic-Settings.md` |
| Código | `solutions/observability/scripts/log-analytics/Enable-AdfDiagnosticSettings.ps1`, `solutions/observability/scripts/log-analytics/Enable-DatabricksDiagnosticSettings.ps1` |
| Quando rodar | Na implantação inicial; quando um Data Factory ou workspace Databricks novo entrar em operação; e periodicamente (por exemplo, mensal) como verificação de conformidade |
| Pré-requisito de | observability-collector (especificação 10): sinal ADF e rede de apoio do Databricks |

## 1. Objetivo funcional

Garantir que todo Data Factory e todo workspace Databricks monitorado envia ao Log Analytics da Observabilidade exatamente as categorias de log que o coletor consome, no formato que ele entende, sem duplicar ingestão e sem mexer em configurações de terceiros. Os scripts também fixam a retenção das tabelas que recebem esses logs, por padrão em 30 dias, o mesmo prazo do hub.

| Recurso | Categorias padrão | Tabela resultante | Modo |
|---|---|---|---|
| Azure Data Factory | `ActivityRuns`, `PipelineRuns`, `TriggerRuns` | `ADFActivityRun` (usada pelo coletor), `ADFPipelineRun`, `ADFTriggerRun` | Resource specific (`Dedicated`) — obrigatório; em modo `AzureDiagnostics` o coletor não enxerga nada |
| Azure Data Factory com Self-Hosted IR próprio (opcional, `-IncludeIntegrationRuntimeSignals`) | métricas `AllMetrics` | `AzureMetrics` (tabela compartilhada; o script não altera a retenção dela) | Sinais do Integration Runtime: nós disponíveis, CPU, memória, fila e atraso de pickup |
| Azure Databricks | `jobs` | `DatabricksJobs` (rede de apoio do coletor) | Tabelas próprias do Databricks; exige workspace **Premium** |

## 2. Comportamento comum aos dois scripts

1. Valida que o workspace Log Analytics informado (por Resource ID) existe.
2. Varre a assinatura atual, as informadas em `-SubscriptionId` ou todas (`-AllSubscriptions`), filtrando por `-ResourceGroupName`, `-NameFilter` (curinga) e `-ExcludeName`.
3. Para cada recurso, confere as categorias suportadas e ignora (com aviso) as que não existem.
4. Procura o Diagnostic Settings de nome `-DiagnosticSettingName` (padrão `diag-observabilidade-law`). Se já estiver conforme (mesmo workspace, categorias habilitadas e, no ADF, modo `Dedicated` e métricas conforme `-IncludeMetrics` ou `-IncludeIntegrationRuntimeSignals`), não faz nada. Senão, cria ou substitui só esse setting.
5. Avisa quando outro Diagnostic Settings do mesmo recurso já envia as mesmas categorias para o mesmo workspace (cada setting gera ingestão própria, então isso dobra o custo). Não remove: não foi o script que o criou.
6. Respeita o limite de 5 Diagnostic Settings por recurso.
7. Erro em um recurso (permissão, lock) não interrompe os demais.
8. Imprime um relatório (`Conforme`, `Criado`, `Atualizado`, `Simulado (...)`, `Ignorado (sku)`, `Erro`), grava CSV com `-ReportPath` e termina com código 1 se houve algum `Erro`.

O script do Databricks também pula, com status `Ignorado (sku)`, workspaces que não são Premium ou Trial.

**Retenção (sempre aplicada).** Antes de varrer os recursos, cada script define a retenção das tabelas das categorias pedidas no workspace de destino: `ADFActivityRun`, `ADFPipelineRun` e `ADFTriggerRun` no script do ADF; `Databricks<Categoria>` (por padrão só `DatabricksJobs`) no do Databricks. O valor vem de `-RetentionInDays`, padrão 30, aceito de 4 a 730, e é gravado tanto na retenção interativa quanto na total (`RetentionInDays` e `TotalRetentionInDays`), para não sobrar arquivamento além do prazo. A retenção é por **tabela**, nunca a padrão do workspace: os workspaces `LA-ANALYTICS-*` são compartilhados, e mudar o padrão afetaria os outros times. Tabela já com o valor certo fica `Conforme`; quando a retenção vai diminuir, o script avisa que os dados mais antigos que o novo prazo serão removidos. A retenção tem relatório próprio, e um erro nela também faz o script terminar com código 1.

**Sinais do Integration Runtime.** Com `-IncludeIntegrationRuntimeSignals`, o script do ADF lista os Integration Runtimes de cada Data Factory e liga as métricas só onde existe Self-Hosted IR **próprio** (tipo `SelfHosted`, sem vínculo a outro Data Factory). IR compartilhado (linked) é ignorado, porque o sinal de nó sai do Data Factory que o hospeda. Nos demais Data Factories as métricas ficam desligadas, e o relatório diz qual caso se aplicou, citando os nomes dos IRs. A métrica principal, `IntegrationRuntimeAvailableNodeNumber`, cai quando um nó fica indisponível. As métricas chegam à tabela `AzureMetrics`, que o coletor ainda não lê (evolução prevista na especificação 10).

## 3. Uso

Pré-requisitos: PowerShell 7, módulos `Az.Accounts`, `Az.Resources`, `Az.Monitor` 4.0 ou superior e `Az.OperationalInsights` 3.2 ou superior (`Install-Module Az -Scope CurrentUser`), `Connect-AzAccount` com uma identidade que tenha `Monitoring Contributor` (ou `Contributor`) nos recursos e `Log Analytics Contributor` no workspace de destino (necessário também para alterar a retenção das tabelas).

```powershell
$la = (Get-AzOperationalInsightsWorkspace -ResourceGroupName RG-ANALYTICS-VCBR -Name LA-ANALYTICS-VCBR).ResourceId

# 1) Simular
./scripts/log-analytics/Enable-AdfDiagnosticSettings.ps1 -LogAnalyticsWorkspaceResourceId $la -AllSubscriptions -NameFilter 'DF-*' -WhatIf
./scripts/log-analytics/Enable-DatabricksDiagnosticSettings.ps1 -LogAnalyticsWorkspaceResourceId $la -NameFilter 'dbr-*-vcbr' -WhatIf

# 2) Aplicar, guardando o relatório
./scripts/log-analytics/Enable-AdfDiagnosticSettings.ps1 -LogAnalyticsWorkspaceResourceId $la -AllSubscriptions -NameFilter 'DF-*' -ReportPath ./adf.csv
./scripts/log-analytics/Enable-DatabricksDiagnosticSettings.ps1 -LogAnalyticsWorkspaceResourceId $la -NameFilter 'dbr-*-vcbr' -ReportPath ./dbx.csv
```

Para ligar também os sinais de Self-Hosted IR, acrescente `-IncludeIntegrationRuntimeSignals`. Para usar outro prazo de retenção, acrescente `-RetentionInDays <dias>`. Em dev, usar `LA-ANALYTICS-DEV-VCBR` e os recursos de dev. Os ADFs não precisam estar na mesma região do workspace.

## 4. Verificação depois de rodar

Os dados começam a chegar em até cerca de 15 minutos, e só quando houver execução. No Log Analytics:

```kql
ADFActivityRun | where TimeGenerated > ago(1h) | summarize count() by DataFactoryName, Status
DatabricksJobs | where TimeGenerated > ago(1h) | summarize count() by ActionName
```

Para conferir a retenção: `Get-AzOperationalInsightsTable -ResourceGroupName RG-ANALYTICS-VCBR -WorkspaceName LA-ANALYTICS-VCBR -TableName ADFActivityRun | Select-Object Name, RetentionInDays, TotalRetentionInDays`.

Se `ADFActivityRun` não existir mas `AzureDiagnostics` tiver linhas de `MICROSOFT.DATAFACTORY`, há um Diagnostic Settings antigo em modo `AzureDiagnostics`; o aviso de duplicidade do script aponta qual.

## 5. Validação feita

Os dois scripts foram analisados pelo parser do PowerShell 7.4 sem erros e executados contra cmdlets Az simulados, cobrindo criação, simulação com `-WhatIf`, recurso já conforme, aviso de setting duplicado, workspace Databricks Standard ignorado e, na retenção, tabela atualizada (com aviso de redução), tabela já conforme e erro de tabela. O trecho dos sinais de Integration Runtime (`-IncludeIntegrationRuntimeSignals`) foi apenas analisado pelo parser; não foi executado nem contra cmdlets simulados. Nenhum script foi executado contra uma assinatura real.

## 6. Pontos de atenção

Custo: `AllMetrics` do ADF entra em `AzureMetrics` e não permite escolher só as métricas do IR; por isso o script liga apenas nos Data Factories com Self-Hosted IR próprio. Cada Data Factory com muitas atividades gera volume relevante em `ADFActivityRun`; a estimativa do projeto está em `01-Volumetria-e-Custos/`. Habilitar categorias adicionais do Databricks (`clusters`, `accounts`, `unityCatalog`) aumenta o custo e não é usado pela solução. Remover a Observabilidade exige remover os settings `diag-observabilidade-law` (`Remove-AzDiagnosticSetting -ResourceId <id> -Name diag-observabilidade-law`).
