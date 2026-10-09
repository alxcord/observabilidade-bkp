// sync-expectations/infra/main.bicep
//
// Container Apps Job do coletor. Recebe de params.json: imageName,
// cronExpression, tags, sqlServerFqdn, sqlDatabase, logAnalyticsWorkspaceId.
// Recebe do pipeline: jobName, environment, imageTag.

param jobName string
param imageName string
param imageTag string
param cronExpression string
param tags object
param environment string
param logAnalyticsWorkspaceId string

// Recursos compartilhados (terraform/platform/), resolvidos por nome
// determinístico — nunca por valor fixo em params.json, que serve os dois
// ambientes ao mesmo tempo e por isso não pode conter algo que só vale
// para um deles.
var sqlServerName = environment == 'prod' ? 'sqlsrv-observ-vcbr' : 'sqlsrv-observ-dev-vcbr'
var sqlServerFqdn = '${sqlServerName}.database.windows.net'
var sqlDatabase = environment == 'prod' ? 'db-observ-vcbr' : 'db-observ-dev-vcbr'

// Recursos compartilhados, já existentes no resource group
param acrLoginServer string = 'cranalyticsvcbr.azurecr.io'
param caeResourceGroup string = resourceGroup().name

var caeName = environment == 'prod' ? 'cae-observ-vcbr' : 'cae-observ-dev-vcbr'

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: caeName
  scope: resourceGroup(caeResourceGroup)
}

// Identidade atribuída pelo usuário, criada fora deste template porque é
// compartilhada e precisa ser registrada no Azure Databricks antes do
// primeiro deploy. Identidade do sistema não serve aqui: ela nasce e morre
// com o recurso, e o registro no Databricks é manual demais para refazer
// a cada recriação do job.
var managedIdentityName = environment == 'prod' ? 'mi-observ-coletor-vcbr' : 'mi-observ-coletor-dev-vcbr'
param managedIdentityResourceGroup string = resourceGroup().name

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
    OBS: 'Observabilidade - sincroniza expectativas de execucao (Databricks e ADF) para deteccao de ausencia'
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
