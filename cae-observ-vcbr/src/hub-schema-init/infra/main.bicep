// hub-schema-init/infra/main.bicep
//
// Container Apps Job MANUAL que cria/atualiza o schema do hub (Azure SQL).
// Recebe de params.json: imageName, tags. Recebe do pipeline: jobName,
// environment, imageTag. SQL, Container Apps Environment e Managed Identity
// são resolvidos por nome determinístico (ver terraform/platform/).
//
// ATENÇÃO À IDENTIDADE. A identidade do job precisa de permissão de DDL no
// banco e, para o passo --grant-users, de ser administradora Microsoft Entra
// do servidor. A identidade padrão dos jobs (mi-observ-coletor-*) só tem
// leitura e escrita. Use um destes dois caminhos, só durante a execução:
//   1. adicionar a identidade ao grupo Entra que é administrador do servidor
//      (sql_aad_admin_object_id em terraform/platform/) e removê-la depois;
//   2. informar, em managedIdentityNameOverride, uma identidade própria que
//      já seja administradora.

param jobName string
param imageName string
param imageTag string
param tags object
param environment string
param managedIdentityNameOverride string = ''

var sqlServerName = environment == 'prod' ? 'sqlsrv-observ-vcbr' : 'sqlsrv-observ-dev-vcbr'
var sqlServerFqdn = '${sqlServerName}.database.windows.net'
var sqlDatabase = environment == 'prod' ? 'db-observ-vcbr' : 'db-observ-dev-vcbr'

param acrLoginServer string = 'cranalyticsvcbr.azurecr.io'
param caeResourceGroup string = resourceGroup().name
param managedIdentityResourceGroup string = resourceGroup().name

var caeName = environment == 'prod' ? 'cae-observ-vcbr' : 'cae-observ-dev-vcbr'
var managedIdentityName = !empty(managedIdentityNameOverride)
  ? managedIdentityNameOverride
  : (environment == 'prod' ? 'mi-observ-coletor-vcbr' : 'mi-observ-coletor-dev-vcbr')

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: caeName
  scope: resourceGroup(caeResourceGroup)
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
    OBS: 'Observabilidade - cria e atualiza o schema do hub (Azure SQL), execucao manual'
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
      triggerType: 'Manual'
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 900
      // Sem retentativa automática: uma falha de DDL precisa ser lida, não repetida.
      replicaRetryLimit: 0
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
            { name: 'AZURE_CLIENT_ID', value: uami.properties.clientId }
            { name: 'SQL_SERVER_FQDN', value: sqlServerFqdn }
            { name: 'SQL_DATABASE', value: sqlDatabase }
            { name: 'ENVIRONMENT', value: environment }
          ]
        }
      ]
    }
  }
}

output jobPrincipalId string = uami.properties.principalId
