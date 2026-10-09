<#
.SYNOPSIS
    Ativa (ou corrige) o Diagnostic Settings dos workspaces Azure Databricks
    para o Log Analytics usado pela Observabilidade.

.DESCRIPTION
    O observability-collector usa a tabela DatabricksJobs do Log Analytics
    como REDE DE APOIO da Jobs API (especificacao
    10-Job-observability-collector.md, passo "reconciliacao"). Essa tabela
    so recebe dados se o workspace tiver um Diagnostic Settings com a
    categoria "jobs" apontando para o workspace Log Analytics.

    Pre-requisito da plataforma: log de diagnostico do Databricks so existe
    no plano PREMIUM (ou TRIAL). Workspaces Standard sao listados no
    relatorio como "Ignorado (sku)" e nao sao alterados.

    O script e idempotente e foi feito para rodar de vez em quando:

      1. descobre os workspaces Databricks nas assinaturas pedidas;
      2. confere o SKU de cada um;
      3. compara o Diagnostic Settings de nome $DiagnosticSettingName com o
         estado desejado e so cria/atualiza o que estiver diferente;
      4. avisa quando outro Diagnostic Settings ja manda as mesmas
         categorias para o mesmo workspace (custo em dobro);
      5. imprime (e opcionalmente grava em CSV) um relatorio por recurso;
      6. define a retencao (padrao 30 dias) das tabelas Databricks* das
         categorias pedidas (DatabricksJobs, por padrao).

    Use -WhatIf para simular.

.PARAMETER LogAnalyticsWorkspaceResourceId
    Resource ID completo do workspace Log Analytics de destino.

.PARAMETER SubscriptionId
    Assinaturas a varrer. Se omitido, usa a do contexto atual.

.PARAMETER AllSubscriptions
    Varre todas as assinaturas visiveis.

.PARAMETER ResourceGroupName
    Restringe a busca a um resource group (opcional).

.PARAMETER NameFilter
    Filtro curinga sobre o nome do workspace. Padrao '*'.
    Exemplo: 'dbr-dataeng01-*'.

.PARAMETER ExcludeName
    Workspaces a ignorar explicitamente.

.PARAMETER DiagnosticSettingName
    Nome do Diagnostic Settings gerenciado por este script.

.PARAMETER Categories
    Categorias de log. Padrao: apenas 'jobs', a unica que a solucao consome.
    Acrescentar outras (clusters, accounts, unityCatalog, ...) aumenta o
    custo de ingestao e deve ser decisao consciente.

.PARAMETER RetentionInDays
    Retencao, em dias, aplicada as tabelas que recebem estes logs no
    workspace de destino. Sempre aplicada; padrao 30 (o prazo de
    retencao do hub, especificacao 01). Aceita de 4 a 730.

.PARAMETER ReportPath
    CSV para gravar o relatorio (opcional).

.EXAMPLE
    ./Enable-DatabricksDiagnosticSettings.ps1 `
        -LogAnalyticsWorkspaceResourceId $laId -NameFilter 'dbr-*-vcbr' -WhatIf

.NOTES
    Permissao necessaria: Contributor (ou papel com
    Microsoft.Databricks/workspaces/assignWorkspaceAdmin/action e
    Microsoft.Insights/diagnosticSettings/write) em cada workspace, e Log
    Analytics Contributor no workspace de destino.
    Modulos: Az.Accounts, Az.Resources, Az.Monitor 4.0 ou superior e
    Az.OperationalInsights 3.2 ou superior.
    Para a retencao: Log Analytics Contributor no workspace de destino.


    Sobre o tipo de destino: o Databricks grava sempre em tabelas proprias
    (DatabricksJobs, DatabricksClusters, ...), por isso este script nao
    define LogAnalyticsDestinationType. Para conferir depois de ~15 min:
        DatabricksJobs | where TimeGenerated > ago(1h) | take 10
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
    [string[]] $Categories = @('jobs'),
    [ValidateRange(4, 730)]
    [int]      $RetentionInDays = 30,
    [string]   $ReportPath
)

$ErrorActionPreference = 'Stop'

# SKUs em que o Databricks publica log de diagnostico.
$SupportedSkus = @('premium', 'trial')

# Limite da plataforma: no maximo 5 Diagnostic Settings por recurso.
$MaxSettingsPerResource = 5


# -----------------------------------------------------------------------------
# Funcoes auxiliares
# -----------------------------------------------------------------------------

function Get-TargetSubscriptions {
    if ($AllSubscriptions) {
        return @(Get-AzSubscription | Where-Object State -eq 'Enabled' |
                 Select-Object -ExpandProperty Id)
    }
    if ($SubscriptionId) { return $SubscriptionId }
    return @((Get-AzContext).Subscription.Id)
}

function Get-EnabledLogCategories($Setting) {
    return @($Setting.Log | Where-Object { $_.Enabled -and $_.Category } |
             Select-Object -ExpandProperty Category)
}

function Test-SettingCompliant($Setting, [string[]]$WantedCategories) {
    # Conforme = mesmo workspace e todas as categorias pedidas habilitadas.
    if (-not $Setting) { return $false }
    if ($Setting.WorkspaceId -ne $LogAnalyticsWorkspaceResourceId) { return $false }
    $enabled = Get-EnabledLogCategories $Setting
    foreach ($category in $WantedCategories) {
        if ($enabled -notcontains $category) { return $false }
    }
    return $true
}

function Get-LogTableName([string]$Category) {
    # O Databricks grava cada categoria numa tabela "Databricks<Categoria>":
    # jobs -> DatabricksJobs, clusters -> DatabricksClusters, ...
    return 'Databricks' + $Category.Substring(0, 1).ToUpper() + $Category.Substring(1)
}

function New-Report($Subscription, $Resource, [string]$Status, [string]$Detail) {
    return [pscustomobject]@{
        Subscription  = $Subscription
        ResourceGroup = $Resource.ResourceGroupName
        Workspace     = $Resource.Name
        Sku           = $Resource.Sku.Name
        Status        = $Status
        Detail        = $Detail
    }
}


# -----------------------------------------------------------------------------
# Execucao
# -----------------------------------------------------------------------------

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
foreach ($table in @($Categories | ForEach-Object { Get-LogTableName $_ })) {
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

    # -ExpandProperties traz o SKU, necessario para a checagem de plano.
    $query = @{ ResourceType = 'Microsoft.Databricks/workspaces'; ExpandProperties = $true }
    if ($ResourceGroupName) { $query.ResourceGroupName = $ResourceGroupName }

    $dbxWorkspaces = @(Get-AzResource @query |
                       Where-Object { $_.Name -like $NameFilter -and $ExcludeName -notcontains $_.Name })

    if ($dbxWorkspaces.Count -eq 0) {
        Write-Host '  nenhum workspace Databricks encontrado com os filtros informados'
        continue
    }

    foreach ($dbx in $dbxWorkspaces) {
        try {
            $sku = "$($dbx.Sku.Name)".ToLower()
            if ($SupportedSkus -notcontains $sku) {
                $report += New-Report $sub $dbx 'Ignorado (sku)' 'log de diagnostico exige plano Premium'
                continue
            }

            $supported = @(Get-AzDiagnosticSettingCategory -ResourceId $dbx.ResourceId |
                           Where-Object CategoryType -eq 'Logs' |
                           Select-Object -ExpandProperty Name)
            $wanted = @($Categories | Where-Object { $supported -contains $_ })
            $unsupported = @($Categories | Where-Object { $supported -notcontains $_ })
            if ($unsupported) {
                Write-Warning "$($dbx.Name): categorias nao suportadas ignoradas: $($unsupported -join ', ')"
            }
            if ($wanted.Count -eq 0) {
                $report += New-Report $sub $dbx 'Erro' 'nenhuma categoria valida'
                continue
            }

            $allSettings = @(Get-AzDiagnosticSetting -ResourceId $dbx.ResourceId -ErrorAction SilentlyContinue)
            $mine = $allSettings | Where-Object Name -eq $DiagnosticSettingName

            foreach ($other in ($allSettings | Where-Object Name -ne $DiagnosticSettingName)) {
                $overlap = @(Get-EnabledLogCategories $other | Where-Object { $wanted -contains $_ })
                if ($overlap -and $other.WorkspaceId -eq $LogAnalyticsWorkspaceResourceId) {
                    Write-Warning ("$($dbx.Name): o setting '$($other.Name)' ja envia " +
                                   "$($overlap -join ', ') para o mesmo workspace (custo em dobro). Avalie remove-lo.")
                }
            }

            if (Test-SettingCompliant $mine $wanted) {
                $report += New-Report $sub $dbx 'Conforme' 'nada a fazer'
                continue
            }

            if (-not $mine -and $allSettings.Count -ge $MaxSettingsPerResource) {
                $report += New-Report $sub $dbx 'Erro' "recurso ja tem $MaxSettingsPerResource Diagnostic Settings (limite da plataforma)"
                continue
            }

            $logs = @($wanted | ForEach-Object {
                New-AzDiagnosticSettingLogSettingsObject -Category $_ -Enabled $true
            })
            $params = @{
                Name        = $DiagnosticSettingName
                ResourceId  = $dbx.ResourceId
                WorkspaceId = $LogAnalyticsWorkspaceResourceId
                Log         = $logs
            }

            $action = if ($mine) { 'Atualizado' } else { 'Criado' }
            if ($PSCmdlet.ShouldProcess($dbx.Name, "$action Diagnostic Settings '$DiagnosticSettingName'")) {
                New-AzDiagnosticSetting @params | Out-Null
                $report += New-Report $sub $dbx $action ($wanted -join ', ')
            }
            else {
                $report += New-Report $sub $dbx "Simulado ($action)" ($wanted -join ', ')
            }
        }
        catch {
            $report += New-Report $sub $dbx 'Erro' $_.Exception.Message
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

if (($report + $retentionReport) | Where-Object Status -eq 'Erro') { exit 1 }
