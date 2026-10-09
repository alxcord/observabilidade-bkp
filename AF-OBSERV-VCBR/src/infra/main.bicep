// API do hub em Azure Functions, plano Flex Consumption.
//
// Flex Consumption porque escala a zero e suporta integração com VNet — é
// o que permite alcançar o Private Endpoint do Azure SQL sem manter
// réplica ligada. O volume aqui é de centenas de chamadas por dia.
//
// SQL, Managed Identity e subnet são resolvidos por nome determinístico
// (ver terraform/platform/) conforme `environment` — não vêm de
// params.json, que serve dev e prod ao mesmo tempo.

param environment string
param tags object

// Nome do recurso, fixo por ambiente — como todo o resto deste projeto,
// não vem de params.json (que serve dev e prod ao mesmo tempo).
var nomeApp = environment == 'prod' ? 'AF-OBSERV-VCBR' : 'AF-OBSERV-DEV-VCBR'

@description('Referências do Key Vault, no formato @Microsoft.KeyVault(...)')
param refTokenRead string
@secure()
param refTokenWrite string

param vnetName string = 'VNET-Prod-VC'
param vnetResourceGroup string
param managedIdentityResourceGroup string = resourceGroup().name

var envSuffix = environment == 'prod' ? '' : '-dev'
var managedIdentityName = 'mi-observ-api${envSuffix}-vcbr'
var subnetName = 'SUBNET-PRIVATE-AF-OBSERV-${environment == 'prod' ? 'PRD' : 'DEV'}-VCBR'
var sqlServerName = environment == 'prod' ? 'sqlsrv-observ-vcbr' : 'sqlsrv-observ-dev-vcbr'
var sqlDatabase = environment == 'prod' ? 'db-observ-vcbr' : 'db-observ-dev-vcbr'
// Mesmo formato de connection string usado pelos Container Apps Jobs
// (pyodbc, token de Managed Identity injetado em runtime — sem senha).
var sqlConnectionString = 'Driver={ODBC Driver 18 for SQL Server};Server=tcp:${sqlServerName}.database.windows.net,1433;Database=${sqlDatabase};Encrypt=yes;'

// Storage Account exigida pelo plano Flex Consumption pra guardar o
// pacote de deploy — específica desta Function App, por isso criada aqui
// (não em infra/platform, que só cobre o que é de fato compartilhado
// entre múltiplos recursos).
var storageAccountName = environment == 'prod' ? 'stobservvcbr' : 'stobservdevvcbr'

resource subnet 'Microsoft.Network/virtualNetworks/subnets@2023-11-01' existing = {
  name: '${vnetName}/${subnetName}'
  scope: resourceGroup(vnetResourceGroup)
}

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: managedIdentityName
  scope: resourceGroup(managedIdentityResourceGroup)
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: resourceGroup().location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

// O container que functionAppConfig.deployment.storage.value referencia
// abaixo ('deployments') não nasce sozinho com a Storage Account — sem
// este recurso, o primeiro `az functionapp deploy` (esteira) falha
// porque o container de destino não existe.
resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${storage.name}/default/deployments'
}

resource plano 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${nomeApp}-plan'
  location: resourceGroup().location
  tags: tags
  sku: {
    tier: 'FlexConsumption'
    name: 'FC1'
  }
  properties: { reserved: true }
}

resource api 'Microsoft.Web/sites@2023-12-01' = {
  name: nomeApp
  location: resourceGroup().location
  tags: union(tags, {
    Environment: environment
    AMBIENTE: environment == 'prod' ? 'PRD' : 'DEV'
    OBS: 'Observabilidade - unico ponto de entrada do hub: leitura de incidentes e CRUD de tbl_cfg_*'
  })
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${uami.id}': {} }
  }
  properties: {
    serverFarmId: plano.id
    // Sem isto, as Key Vault references de TOKEN_READ e TOKEN_WRITE são
    // resolvidas pela identidade de sistema — que este app não tem, porque
    // usa identidade atribuída. A referência não resolveria e o App Setting
    // ficaria com o texto literal '@Microsoft.KeyVault(...)'. A API recusa
    // esse valor (ver auth.py), então o efeito seria 401 em tudo.
    keyVaultReferenceIdentity: uami.id
    // Integração com VNet: é assim que a Function alcança o Private
    // Endpoint do banco sem expor o SQL.
    virtualNetworkSubnetId: subnet.id
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}deployments'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: uami.id
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
      runtime: { name: 'python', version: '3.11' }
    }
    siteConfig: {
      // Restrição de rede na escrita é requisito: Bearer estático não
      // expira sozinho. Preencher com os IPs de saída da rede corporativa
      // antes de publicar — não mais os do Databricks: sem a rota de
      // ingestão (removida, ver README.md), nada aqui é chamado por ele.
      ipSecurityRestrictionsDefaultAction: 'Deny'
      appSettings: [
        { name: 'AZURE_CLIENT_ID', value: uami.properties.clientId }
        { name: 'SQL_CONNECTION_STRING', value: sqlConnectionString }
        { name: 'TOKEN_READ', value: refTokenRead }
        { name: 'TOKEN_WRITE', value: refTokenWrite }
        { name: 'ENVIRONMENT', value: environment }
        // O host do Functions não inicia no Flex Consumption sem storage
        // próprio. Vai por identidade, não por connection string: chave de
        // conta em App Setting é segredo de longa duração sem rotação.
        // Exige 'Storage Blob Data Owner' da identidade nesta conta.
        { name: 'AzureWebJobsStorage__accountName', value: storage.name }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'AzureWebJobsStorage__clientId', value: uami.properties.clientId }
      ]
    }
  }
}

output principalId string = uami.properties.principalId
output hostname string = api.properties.defaultHostName
