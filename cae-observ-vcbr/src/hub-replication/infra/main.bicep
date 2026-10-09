// hub-replication/infra/main.bicep
//
// Container Apps Job da replicação Azure SQL → Delta. Recebe de
// params.json: imageName, cronExpression, tags, databricksWarehouseId.
// Recebe do pipeline: jobName, environment, imageTag. SQL, Container Apps
// Environment, Managed Identity e Databricks Host são resolvidos por nome
// determinístico (ver terraform/platform/) — nunca digitados à mão
// nem vindos de params.json, que serve dev e prod ao mesmo tempo.
//
// Não fala JDBC com o Databricks — só HTTPS público (Files API e Statement
// Execution API). Por isso não precisa de Network Connectivity
// Configuration nem de VNet injection no workspace: ver a nota no topo de
// hub_replication.py.

param jobName string
param imageName string
param imageTag string
param cronExpression string
param tags object
param environment string
param databricksWarehouseId string

// SQL resolvido por nome determinístico (terraform/platform/) — não
// vem de params.json porque esse arquivo serve dev e prod ao mesmo tempo.
var sqlServerName = environment == 'prod' ? 'sqlsrv-observ-vcbr' : 'sqlsrv-observ-dev-vcbr'
var sqlServerFqdn = '${sqlServerName}.database.windows.net'
var sqlDatabase = environment == 'prod' ? 'db-observ-vcbr' : 'db-observ-dev-vcbr'

// Workspace Databricks que executa os jobs de observabilidade — recurso já
// existente, nunca criado por este template. Um por ambiente:
// dbr-dataeng01-dev-vcbr (dev) e dbr-dataeng01-vcbr (prod). Vazio (default)
// escolhe pelo mesmo `environment` que o pipeline já passa — um parâmetro
// não pode ter default que referencie outro parâmetro, por isso a escolha
// mora na variável abaixo, não aqui. Um único params.json serve os dois
// ambientes sem duplicar arquivo.
param databricksWorkspaceNameOverride string = ''

var databricksWorkspaceName = !empty(databricksWorkspaceNameOverride)
  ? databricksWorkspaceNameOverride
  : (environment == 'prod' ? 'dbr-dataeng01-vcbr' : 'dbr-dataeng01-dev-vcbr')

// Resource group do workspace acima. Default assume o mesmo RG deste
// deploy — AJUSTAR se o Databricks vive em resource group separado (comum
// quando a plataforma de dados é provisionada por outro time/pipeline).
param databricksWorkspaceResourceGroup string = resourceGroup().name

resource databricksWorkspace 'Microsoft.Databricks/workspaces@2024-05-01' existing = {
  name: databricksWorkspaceName
  scope: resourceGroup(databricksWorkspaceResourceGroup)
}

// Recursos compartilhados (terraform/platform/), resolvidos por nome
// determinístico conforme `environment`.
param acrLoginServer string = 'cranalyticsvcbr.azurecr.io'
param caeResourceGroup string = resourceGroup().name
param managedIdentityResourceGroup string = resourceGroup().name

var caeName = environment == 'prod' ? 'cae-observ-vcbr' : 'cae-observ-dev-vcbr'
// Mesma identidade do coletor e do sync-expectations: já registrada no
// Azure Databricks (plano de controle, para as chamadas REST) e com
// permissão no Azure SQL.
var managedIdentityName = environment == 'prod' ? 'mi-observ-coletor-vcbr' : 'mi-observ-coletor-dev-vcbr'

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
    OBS: 'Observabilidade - replica o hub (Azure SQL) para Delta e avanca o watermark que destrava a purga'
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
        parallelism: 1
        replicaCompletionCount: 1
      }
      // Lote horário, duas tabelas, com upload de arquivo e polling de
      // Statement Execution — folga generosa em relação ao coletor.
      replicaTimeout: 1800
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
            // Sem segredo: a autenticação em Databricks e Azure SQL é
            // feita pela managed identity acima.
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
              // properties.workspaceUrl devolve só o host (sem esquema) —
              // hub_replication.py espera a URL completa.
              name: 'DATABRICKS_HOST'
              value: 'https://${databricksWorkspace.properties.workspaceUrl}'
            }
            {
              name: 'DATABRICKS_WAREHOUSE_ID'
              value: databricksWarehouseId
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
