<#
.SYNOPSIS
    Ativa (ou corrige) o Diagnostic Settings dos Azure Data Factories para o
    Log Analytics usado pela Observabilidade.

.DESCRIPTION
    O observability-collector le o sinal do ADF na tabela ADFActivityRun do
    Log Analytics (especificacao 10-Job-observability-collector.md). Essa
    tabela so existe se cada Data Factory tiver um Diagnostic Settings
    enviando a categoria ActivityRuns em modo "resource specific"
    (LogAnalyticsDestinationType = Dedicated). Em modo AzureDiagnostics os
    dados vao para a tabela generica AzureDiagnostics e o coletor nao os ve.

    O script e idempotente e foi feito para rodar de vez em quando (por
    exemplo, uma vez por mes ou quando um ADF novo entrar em operacao):

      1. descobre os Data Factories nas assinaturas/resource groups pedidos;
      2. para cada um, compara o Diagnostic Settings de nome
         $DiagnosticSettingName com o estado desejado;
      3. cria ou atualiza so o que estiver diferente, e nao mexe no que ja
         esta conforme;
      4. avisa quando OUTRO Diagnostic Settings do mesmo recurso ja envia as
         mesmas categorias para o mesmo workspace (ingestao em dobro = custo
         em dobro), sem remover nada que nao tenha sido criado por ele;
      5. imprime (e opcionalmente grava em CSV) um relatorio por recurso;
      6. com -IncludeIntegrationRuntimeSignals, liga tambem as metricas
         (AllMetrics) nos Data Factories que possuem Self-Hosted Integration
         Runtime PROPRIO, para o sinal de no indisponivel chegar ao Log
         Analytics (tabela AzureMetrics). Integration Runtimes compartilhados
         (linked) de outro Data Factory sao ignorados: o sinal sai do ADF dono;
      7. define a retencao (padrao 30 dias) das tabelas ADFActivityRun,
         ADFPipelineRun e ADFTriggerRun no workspace de destino.

    Use -WhatIf para ver o que seria feito sem alterar nada.

.PARAMETER LogAnalyticsWorkspaceResourceId
    Resource ID completo do workspace de destino, por exemplo
    /subscriptions/<sub>/resourceGroups/RG-ANALYTICS-VCBR/providers/
    Microsoft.OperationalInsights/workspaces/LA-ANALYTICS-VCBR.
    Pedido por Resource ID (e nao por nome) para nunca haver ambiguidade
    entre workspaces de mesmo nome em assinaturas diferentes.

.PARAMETER SubscriptionId
    Uma ou mais assinaturas a varrer. Se omitido, usa a assinatura do
    contexto atual (Get-AzContext). Ignorado com -AllSubscriptions.

.PARAMETER AllSubscriptions
    Varre todas as assinaturas visiveis para a identidade logada.

.PARAMETER ResourceGroupName
    Restringe a busca a um resource group (opcional).

.PARAMETER NameFilter
    Filtro curinga sobre o nome do Data Factory. Padrao '*' (todos).
    Exemplo: 'DF-*-VCBR'.

.PARAMETER ExcludeName
    Nomes de Data Factory a ignorar explicitamente.

.PARAMETER DiagnosticSettingName
    Nome do Diagnostic Settings gerenciado por este script. Mantenha o
    mesmo nome entre execucoes: e por ele que o script reconhece o que e seu.

.PARAMETER Categories
    Categorias de log. Padrao: ActivityRuns, PipelineRuns e TriggerRuns.
    So ActivityRuns e consumida pelo coletor hoje; as outras duas sao
    baratas e servem para investigacao (e foram previstas no desenho, §3.2).

.PARAMETER IncludeMetrics
    Envia tambem AllMetrics. Desligado por padrao: a solucao nao usa
    metricas do ADF e elas so aumentam o custo de ingestao.

.PARAMETER IncludeIntegrationRuntimeSignals
    Liga AllMetrics so nos Data Factories com Self-Hosted Integration Runtime
    proprio (tipo SelfHosted, sem vinculo a outro Data Factory). As metricas
    do IR (nos disponiveis, CPU, memoria, fila e atraso de pickup) vao para a
    tabela AzureMetrics, que e compartilhada no workspace: o script NAO altera
    a retencao dela. Nos demais Data Factories as metricas ficam desligadas.
    Nao e necessario junto com -IncludeMetrics, que liga em todos.

.PARAMETER RetentionInDays
    Retencao, em dias, aplicada as tabelas que recebem estes logs no
    workspace de destino. Sempre aplicada; padrao 30 (o prazo de
    retencao do hub, especificacao 01). Aceita de 4 a 730.

.PARAMETER ReportPath
    Caminho de um CSV para gravar o relatorio (opcional).

.EXAMPLE
    # Simulacao em producao, todos os ADFs da assinatura atual
    ./Enable-AdfDiagnosticSettings.ps1 `
        -LogAnalyticsWorkspaceResourceId $laId -WhatIf

.EXAMPLE
    # Execucao real, so os ADFs VCBR de um resource group, com relatorio
    ./Enable-AdfDiagnosticSettings.ps1 `
        -LogAnalyticsWorkspaceResourceId $laId `
        -ResourceGroupName RG-ADF-VCBR -NameFilter 'DF-*-VCBR' `
        -ReportPath ./relatorio-adf.csv

.NOTES
    Permissao necessaria: Monitoring Contributor (ou Contributor) em cada
    Data Factory, e Log Analytics Contributor no workspace de destino.
    Modulos: Az.Accounts, Az.Resources, Az.Monitor 4.0 ou superior e
    Az.OperationalInsights 3.2 ou superior.
    Para a retencao: Log Analytics Contributor no workspace de destino.

#>
#Requires -Modules Az.Accounts, Az.Resources
#Requires -Modules @{ ModuleName = 'Az.Monitor'; ModuleVersion = '4.0.0' }
#Requires -Modules @{ ModuleName = 'Az.OperationalInsights'; ModuleVersion = '3.2.0' }

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]   $LogAnalyticsWorkspaceResourceId,

    [string[]] $SubscriptionId,
    [switch]   $AllSubscriptions,
    [string]   $ResourceGroupName,
    [string]   $NameFilter = '*',
    [string[]] $ExcludeName = @(),
    [string]   $DiagnosticSettingName = 'diag-observabilidade-law',
    [string[]] $Categories = @('ActivityRuns', 'PipelineRuns', 'TriggerRuns'),
    [switch]   $IncludeMetrics,
    [switch]   $IncludeIntegrationRuntimeSignals,
    [ValidateRange(4, 730)]
    [int]      $RetentionInDays = 30,
    [string]   $ReportPath
)

$ErrorActionPreference = 'Stop'

# Modo "resource specific": grava em ADFActivityRun/ADFPipelineRun/ADFTriggerRun.
# E o unico modo que o coletor entende.
$DestinationType = 'Dedicated'

# Limite da plataforma: no maximo 5 Diagnostic Settings por recurso.
$MaxSettingsPerResource = 5


# -----------------------------------------------------------------------------
# Funcoes auxiliares
# -----------------------------------------------------------------------------

function Get-TargetSubscriptions {
    # Decide quais assinaturas varrer, na ordem de precedencia dos parametros.
    if ($AllSubscriptions) {
        return @(Get-AzSubscription | Where-Object State -eq 'Enabled' |
                 Select-Object -ExpandProperty Id)
    }
    if ($SubscriptionId) { return $SubscriptionId }
    return @((Get-AzContext).Subscription.Id)
}

function Get-EnabledLogCategories($Setting) {
    # Categorias de log habilitadas num Diagnostic Settings existente.
    return @($Setting.Log | Where-Object { $_.Enabled -and $_.Category } |
             Select-Object -ExpandProperty Category)
}

function Test-SettingCompliant($Setting, [string[]]$WantedCategories, [bool]$WantMetrics) {
    # O Diagnostic Settings ja esta exatamente como queremos?
    if (-not $Setting) { return $false }
    if ($Setting.WorkspaceId -ne $LogAnalyticsWorkspaceResourceId) { return $false }
    if ($Setting.LogAnalyticsDestinationType -ne $DestinationType) { return $false }

    $enabled = Get-EnabledLogCategories $Setting
    foreach ($category in $WantedCategories) {
        if ($enabled -notcontains $category) { return $false }
    }

    $metricsOn = @($Setting.Metric | Where-Object Enabled).Count -gt 0
    return ($metricsOn -eq $WantMetrics)
}

function Get-LogTableName([string]$Category) {
    # Tabela "resource specific" que recebe cada categoria do ADF.
    $map = @{
        'ActivityRuns' = 'ADFActivityRun'
        'PipelineRuns' = 'ADFPipelineRun'
        'TriggerRuns'  = 'ADFTriggerRun'
    }
    return $map[$Category]
}

function Get-OwnSelfHostedIntegrationRuntimes($Factory) {
    # Nomes dos Integration Runtimes Self-Hosted proprios do Data Factory.
    # Linked (compartilhado de outro Data Factory) fica de fora: tem
    # typeProperties.linkedInfo e o sinal de no sai do ADF que o hospeda.
    $names = @()
    $path = "$($Factory.ResourceId)/integrationRuntimes?api-version=2018-06-01"
    while ($path) {
        $response = Invoke-AzRestMethod -Method GET -Path $path
        if ($response.StatusCode -ne 200) {
            throw "nao foi possivel listar os Integration Runtimes (HTTP $($response.StatusCode))"
        }
        $body = $response.Content | ConvertFrom-Json
        foreach ($ir in $body.value) {
            $isLinked = $null -ne $ir.properties.typeProperties.linkedInfo
            if ($ir.properties.type -eq 'SelfHosted' -and -not $isLinked) { $names += $ir.name }
        }
        $path = if ($body.nextLink) { ([uri]$body.nextLink).PathAndQuery } else { $null }
    }
    return $names
}

function New-Report($Subscription, $Resource, [string]$Status, [string]$Detail) {
    return [pscustomobject]@{
        Subscription = $Subscription
        ResourceGroup = $Resource.ResourceGroupName
        DataFactory  = $Resource.Name
        Status       = $Status
        Detail       = $Detail
    }
}


# -----------------------------------------------------------------------------
# Execucao
# -----------------------------------------------------------------------------

# Valida o workspace de destino antes de tocar em qualquer ADF: um ID errado
# criaria Diagnostic Settings apontando para lugar nenhum.
$workspace = Get-AzResource -ResourceId $LogAnalyticsWorkspaceResourceId -ErrorAction SilentlyContinue
if (-not $workspace) {
    throw "Workspace Log Analytics nao encontrado: $LogAnalyticsWorkspaceResourceId"
}
Write-Host "Destino: $($workspace.Name) ($($workspace.Location))"

# -----------------------------------------------------------------------------
# Retencao das tabelas no Log Analytics
#
# Feita por TABELA, nunca no workspace inteiro: os workspaces LA-ANALYTICS-*
# sao compartilhados, e mudar a retencao padrao deles afetaria todos os
# outros times que gravam ali. RetentionInDays e TotalRetentionInDays recebem
# o mesmo valor, para nao sobrar arquivamento (archive) alem do prazo.
# -----------------------------------------------------------------------------

# O Resource ID tem a forma
# /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.OperationalInsights/workspaces/<nome>
$idParts = $LogAnalyticsWorkspaceResourceId.Trim('/').Split('/')
$laSubscription  = $idParts[1]
$laResourceGroup = $idParts[3]
$laWorkspaceName = $idParts[-1]
Set-AzContext -SubscriptionId $laSubscription | Out-Null

$retentionReport = @()
foreach ($table in @($Categories | ForEach-Object { Get-LogTableName $_ } | Where-Object { $_ })) {
    try {
        $current = Get-AzOperationalInsightsTable -ResourceGroupName $laResourceGroup `
                       -WorkspaceName $laWorkspaceName -TableName $table
        $before = "$($current.RetentionInDays)/$($current.TotalRetentionInDays)"

        if ($current.RetentionInDays -eq $RetentionInDays -and
            $current.TotalRetentionInDays -eq $RetentionInDays) {
            $retentionReport += [pscustomobject]@{ Table = $table; Status = 'Conforme'; Detail = "$RetentionInDays dias" }
            continue
        }

        # Reduzir a retencao apaga o que ja passou do novo prazo: avisa antes.
        if ($current.TotalRetentionInDays -gt $RetentionInDays) {
            Write-Warning ("$($table): a retencao cai de $($current.TotalRetentionInDays) para " +
                           "$RetentionInDays dias; dados mais antigos que isso serao removidos.")
        }

        if ($PSCmdlet.ShouldProcess("$laWorkspaceName/$table", "Definir retencao de $RetentionInDays dias")) {
            Update-AzOperationalInsightsTable -ResourceGroupName $laResourceGroup `
                -WorkspaceName $laWorkspaceName -TableName $table `
                -RetentionInDays $RetentionInDays -TotalRetentionInDays $RetentionInDays | Out-Null
            $retentionReport += [pscustomobject]@{ Table = $table; Status = 'Atualizado'; Detail = "de $before para $RetentionInDays dias (interativa/total)" }
        }
        else {
            $retentionReport += [pscustomobject]@{ Table = $table; Status = 'Simulado (Atualizado)'; Detail = "de $before para $RetentionInDays dias (interativa/total)" }
        }
    }
    catch {
        $retentionReport += [pscustomobject]@{ Table = $table; Status = 'Erro'; Detail = $_.Exception.Message }
    }
}
Write-Host "`nRetencao das tabelas em $($laWorkspaceName):"
$retentionReport | Format-Table -AutoSize | Out-String -Width 4096 | Write-Host

$report = @()

foreach ($sub in Get-TargetSubscriptions) {
    Set-AzContext -SubscriptionId $sub | Out-Null
    Write-Host "`nAssinatura $sub"

    $query = @{ ResourceType = 'Microsoft.DataFactory/factories' }
    if ($ResourceGroupName) { $query.ResourceGroupName = $ResourceGroupName }

    $factories = @(Get-AzResource @query |
                   Where-Object { $_.Name -like $NameFilter -and $ExcludeName -notcontains $_.Name })

    if ($factories.Count -eq 0) {
        Write-Host '  nenhum Data Factory encontrado com os filtros informados'
        continue
    }

    foreach ($adf in $factories) {
        try {
            # Confere as categorias que o recurso suporta, para nao falhar
            # numa categoria inexistente (ex.: nome digitado errado).
            $supported = @(Get-AzDiagnosticSettingCategory -ResourceId $adf.ResourceId |
                           Where-Object CategoryType -eq 'Logs' |
                           Select-Object -ExpandProperty Name)
            $wanted = @($Categories | Where-Object { $supported -contains $_ })
            $unsupported = @($Categories | Where-Object { $supported -notcontains $_ })
            if ($unsupported) {
                Write-Warning "$($adf.Name): categorias nao suportadas ignoradas: $($unsupported -join ', ')"
            }
            if ($wanted.Count -eq 0) {
                $report += New-Report $sub $adf 'Erro' 'nenhuma categoria valida'
                continue
            }

            $allSettings = @(Get-AzDiagnosticSetting -ResourceId $adf.ResourceId -ErrorAction SilentlyContinue)
            $mine = $allSettings | Where-Object Name -eq $DiagnosticSettingName

            # Aviso de ingestao duplicada: outro setting mandando a mesma
            # categoria para o mesmo workspace dobra o custo. Nao removemos
            # porque nao fomos nos que o criamos.
            foreach ($other in ($allSettings | Where-Object Name -ne $DiagnosticSettingName)) {
                $overlap = @(Get-EnabledLogCategories $other | Where-Object { $wanted -contains $_ })
                if ($overlap -and $other.WorkspaceId -eq $LogAnalyticsWorkspaceResourceId) {
                    Write-Warning ("$($adf.Name): o setting '$($other.Name)' ja envia " +
                                   "$($overlap -join ', ') para o mesmo workspace (custo em dobro). Avalie remove-lo.")
                }
            }

            # Metricas: em todos (-IncludeMetrics) ou so onde ha Self-Hosted IR proprio.
            $wantMetrics = [bool]$IncludeMetrics
            $irNote = ''
            if ($IncludeIntegrationRuntimeSignals -and -not $IncludeMetrics) {
                $ownIrs = @(Get-OwnSelfHostedIntegrationRuntimes $adf)
                if ($ownIrs.Count -gt 0) {
                    $wantMetrics = $true
                    $irNote = "metricas de no dos Self-Hosted IR: $($ownIrs -join ', ')"
                }
                else {
                    $irNote = 'sem Self-Hosted IR proprio: metricas desligadas'
                }
            }

            if (Test-SettingCompliant $mine $wanted $wantMetrics) {
                $report += New-Report $sub $adf 'Conforme' (@('nada a fazer', $irNote) -ne '' -join '; ')
                continue
            }

            if (-not $mine -and $allSettings.Count -ge $MaxSettingsPerResource) {
                $report += New-Report $sub $adf 'Erro' "recurso ja tem $MaxSettingsPerResource Diagnostic Settings (limite da plataforma)"
                continue
            }

            # Monta o estado desejado. New-AzDiagnosticSetting com o mesmo
            # nome substitui o setting existente por inteiro.
            $logs = @($wanted | ForEach-Object {
                New-AzDiagnosticSettingLogSettingsObject -Category $_ -Enabled $true
            })
            $params = @{
                Name                        = $DiagnosticSettingName
                ResourceId                  = $adf.ResourceId
                WorkspaceId                 = $LogAnalyticsWorkspaceResourceId
                Log                         = $logs
                LogAnalyticsDestinationType = $DestinationType
            }
            if ($wantMetrics) {
                $params.Metric = @(New-AzDiagnosticSettingMetricSettingsObject -Category 'AllMetrics' -Enabled $true)
            }

            $detail = (@($wanted -join ', ', $irNote) -ne '') -join '; '
            $action = if ($mine) { 'Atualizado' } else { 'Criado' }
            if ($PSCmdlet.ShouldProcess($adf.Name, "$action Diagnostic Settings '$DiagnosticSettingName'")) {
                New-AzDiagnosticSetting @params | Out-Null
                $report += New-Report $sub $adf $action $detail
            }
            else {
                $report += New-Report $sub $adf "Simulado ($action)" $detail
            }
        }
        catch {
            # Um ADF com problema (permissao, lock) nao impede os demais.
            $report += New-Report $sub $adf 'Erro' $_.Exception.Message
        }
    }
}

# Out-String com largura fixa: renderiza a tabela mesmo sem console
# interativo (esteira, agendador), onde Format-Table sozinho pode sair vazio.
$report | Format-Table -AutoSize | Out-String -Width 4096 | Write-Host
if ($ReportPath) {
    $report | Export-Csv -Path $ReportPath -NoTypeInformation -Encoding UTF8
    Write-Host "Relatorio gravado em $ReportPath"
}

# Codigo de saida diferente de zero quando algum recurso falhou, para quem
# rodar o script numa esteira perceber.
if (($report + $retentionReport) | Where-Object Status -eq 'Erro') { exit 1 }
