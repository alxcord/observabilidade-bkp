// observability-collector/infra/main.bicep
//
// Container Apps Job do coletor. Recebe de params.json: imageName,
// cronExpression, tags, logAnalyticsWorkspaceId, logAnalyticsIngestionEndpoint,
// logAnalyticsDcrImmutableId. Recebe do pipeline: jobName, environment, imageTag.
// SQL, Key Vault, Managed Identity e Container Apps Environment são
// resolvidos por nome determinístico (ver terraform/platform/) — não
// vêm de params.json porque esse arquivo serve dev e prod ao mesmo tempo, e
// esses quatro variam por ambiente.

param jobName string
param imageName string
param imageTag string
param cronExpression string
param tags object
param environment string
param logAnalyticsWorkspaceId string

// Logs Ingestion API do heartbeat (§12) — Data Collection Endpoint e Data
// Collection Rule, provisionados fora deste Bicep (mesma fronteira que já
// vale para os workspaces Databricks de tbl_cfg_workspaces). Vazio por
// padrão: sem os dois, o heartbeat loga um aviso e o ciclo segue normalmente
// (ver collector/heartbeat.py).
param logAnalyticsIngestionEndpoint string = ''
param logAnalyticsDcrImmutableId string = ''

// ---------------------------------------------------------------------------
// Recursos compartilhados (terraform/platform/), resolvidos por nome
// determinístico conforme `environment`.
// ---------------------------------------------------------------------------

param acrLoginServer string = 'cranalyticsvcbr.azurecr.io'
param caeResourceGroup string = resourceGroup().name
param keyVaultResourceGroup string = resourceGroup().name
param managedIdentityResourceGroup string = resourceGroup().name

var sqlServerName = environment == 'prod' ? 'sqlsrv-observ-vcbr' : 'sqlsrv-observ-dev-vcbr'
var sqlServerFqdn = '${sqlServerName}.database.windows.net'
var sqlDatabase = environment == 'prod' ? 'db-observ-vcbr' : 'db-observ-dev-vcbr'
var caeName = environment == 'prod' ? 'cae-observ-vcbr' : 'cae-observ-dev-vcbr'
var managedIdentityName = environment == 'prod' ? 'mi-observ-coletor-vcbr' : 'mi-observ-coletor-dev-vcbr'
// Access Policy do Key Vault (não RBAC) já concede leitura de segredo pra
// esta identidade — ver terraform/platform/.
var keyVaultName = environment == 'prod' ? 'KV-ANALYTICS-VCBR' : 'KV-DEV-ANALYTICS-VCBR'

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: caeName
  scope: resourceGroup(caeResourceGroup)
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
  scope: resourceGroup(keyVaultResourceGroup)
}

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: managedIdentityName
  scope: resourceGroup(managedIdentityResourceGroup)
}

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  tags: union(tags, {
    Environment: environment
    AMBIENTE: environment == 'prod' ? 'PRD' : 'DEV'
    OBS: 'Observabilidade - consulta Jobs API do Databricks e Log Analytics, resolve incidente, grava heartbeat'
  })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uami.id}': {}
    }
  }
  properties: {
    environmentId: cae.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: cronExpression
        // parallelism: 1 é requisito, não preferência. Um ciclo que passe
        // do intervalo faria dois coletores concorrerem pelo mesmo
        // watermark e pela mesma reconciliação de active_executions.
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 600
      replicaRetryLimit: 1
      registries: [
        {
          server: acrLoginServer
          identity: uami.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: jobName
          image: '${acrLoginServer}/${imageName}:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            // Sem segredo: a autenticação em Databricks, Azure SQL e Log
            // Analytics é feita pela managed identity acima.
            {
              name: 'AZURE_CLIENT_ID'
              value: uami.properties.clientId
            }
            {
              name: 'SQL_SERVER_FQDN'
              value: sqlServerFqdn
            }
            {
              name: 'SQL_DATABASE'
              value: sqlDatabase
            }
            {
              name: 'LOG_ANALYTICS_WORKSPACE_ID'
              value: logAnalyticsWorkspaceId
            }
            {
              name: 'KEY_VAULT_URI'
              value: keyVault.properties.vaultUri
            }
            {
              name: 'LOG_ANALYTICS_INGESTION_ENDPOINT'
              value: logAnalyticsIngestionEndpoint
            }
            {
              name: 'LOG_ANALYTICS_DCR_IMMUTABLE_ID'
              value: logAnalyticsDcrImmutableId
            }
            {
              name: 'ENVIRONMENT'
              value: environment
            }
          ]
        }
      ]
    }
  }
}

output jobPrincipalId string = uami.properties.principalId
