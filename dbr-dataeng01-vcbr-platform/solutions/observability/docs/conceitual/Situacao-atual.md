
# Situação atual

## Lista de Workspaces databricks

Obtido diretamente do cloudshell do Azure:

```powershell
az resource list --resource-type "Microsoft.Databricks/workspaces" \
  --query "[].[name, resourceGroup, location]" \
  --output tsv | awk 'BEGIN {print "| Nome | RG | Região |"; print "|---|---|---|"} {print "| "$1" | "$2" | "$3" |"}'
  ```

| Nome | RG | Região |
|---|---|---|
| dbr-neg-comercial-dev-vcbr | RG-DATA-COMERCIAL-DEV-VCBR | brazilsouth |
| dbr-neg-comercial-prd-vcbr | RG-DATA-COMERCIAL-PRD-VCBR | brazilsouth |
| dbr-dataeng01-vcbr | rg-analytics-vcbr | brazilsouth |
| dbr-dataeng01-dev-vcbr | rg-analytics-dev-vcbr | brazilsouth |
| dbr-dataeng03-vcbr | rg-analytics-vcbr | brazilsouth |
| dbr-dataeng02-vcbr | rg-analytics-vcbr | brazilsouth |
| dbr-dataeng03-dev-vcbr | rg-analytics-dev-vcbr | brazilsouth |
| dbr-datasci-vcbr | rg-datasci-vcbr | brazilsouth |
| dbr-dataeng02-dev-vcbr | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| dbvcasup | rg-vc-supply | brazilsouth |
| dbr-consulta-vcbr | RG-ANALYTICS-VCBR | brazilsouth |
| dbr-neg-controladoria-dev-vcbr | RG-DATA-controladoria-DEV-VCBR | brazilsouth |
| dbr-neg-controladoria-prd-vcbr | RG-DATA-controladoria-PRD-VCBR | brazilsouth |
| dbr-neg-engemix-dev-vcbr | RG-DATA-ENGEMIX-DEV-VCBR | brazilsouth |
| dbr-neg-engemix-prd-vcbr | RG-DATA-ENGEMIX-PRD-VCBR | brazilsouth |
| dbr-neg-industrial-dev-vcbr | RG-DATA-INDUSTRIAL-DEV-VCBR | brazilsouth |
| dbr-neg-industrial-prd-vcbr | RG-DATA-INDUSTRIAL-PRD-VCBR | brazilsouth |
| dbr-dataeng04-dev-vcbr | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| dbr-dataeng04-vcbr | RG-ANALYTICS-VCBR | brazilsouth |
| dbr-aiservices-vcbr | RG-VC-AISERVICES-LAB | brazilsouth |
| dbr-neg-logistica-dev-vcbr | RG-DATA-LOGISTICA-DEV-VCBR | brazilsouth |
| dbr-neg-logistica-prd-vcbr | RG-DATA-LOGISTICA-PRD-VCBR | brazilsouth |
| dbr-aiserv-dev-vcbr | RG-VC-AISERVICES-LAB | brazilsouth |
| dbr-aiserv-prd-vcbr | RG-VC-AISERVICES-PRD | brazilsouth |




Com algumas exceções, legados como "dbvcasup", a maioria faz parte do nosso unity catalog que já existe no nosso ambiente como podemos ver mais abaixo.

## Lista de Azure Data Factories

```powershell
az resource list --resource-type "Microsoft.DataFactory/factories" \
  --query "[].[name, resourceGroup, location]" \
  --output tsv | awk 'BEGIN {print "| Nome | RG | Região |"; print "|---|---|---|"} {print "| "$1" | "$2" | "$3" |"}'
```

| Nome | RG | Região |
|---|---|---|
| DF-NEG-COMERCIAL-DEV-VCBR | RG-DATA-COMERCIAL-DEV-VCBR | brazilsouth |
| DF-NEG-COMERCIAL-PRD-VCBR | RG-DATA-COMERCIAL-PRD-VCBR | brazilsouth |
| DF-PUBLIC-VCBR | rg-analytics-vcbr | eastus |
| DF-SUSTENTAB-DEV-VCBR | rg-analytics-dev-vcbr | brazilsouth |
| DF-TI-DEV-VCBR | rg-analytics-dev-vcbr | brazilsouth |
| DF-COMERCIAL-VCBR | rg-analytics-vcbr | brazilsouth |
| DF-CONTROL-DEV-VCBR | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| DF-GENTE-DEV-VCBR | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| DF-LOGISTICS-VCBR | rg-analytics-vcbr | eastus |
| DF-TI-VCBR | rg-analytics-vcbr | brazilsouth |
| df-datasci-sandbox-vcbr-eastus | rg-datasci-vcbr | eastus |
| DF-PIOSI-VCBR | rg-analytics-vcbr | brazilsouth |
| DF-GENTE-VCBR | RG-ANALYTICS-VCBR | brazilsouth |
| DF-MINING-DEV-VCBR | rg-analytics-dev-vcbr | brazilsouth |
| DF-INDUSTRIAL-VCBR | rg-analytics-vcbr | brazilsouth |
| DF-INTEGRATION-RT-VCBR | rg-analytics-vcbr | brazilsouth |
| df-datasci-sandbox-vcbr | rg-datasci-vcbr | brazilsouth |
| DF-COMERCIAL2-VCBR | RG-ANALYTICS-VCBR | brazilsouth |
| DF-SUSTENTAB-VCBR | rg-analytics-vcbr | brazilsouth |
| DF-COMERCIAL2-DEV-VCBR | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| DF-ENGEMIX-DEV-VCBR | rg-analytics-dev-vcbr | brazilsouth |
| DF-PIOSI-DEV-VCBR | rg-analytics-dev-vcbr | brazilsouth |
| DF-CONTROL-VCBR | RG-ANALYTICS-VCBR | brazilsouth |
| DF-LOGISTICS-DEV-VCBR | rg-analytics-dev-vcbr | brazilsouth |
| V2DF | rg-sqlcrc-blobstorage | eastus |
| dfbivcasup | rg-vc-supply | brazilsouth |
| dfvcasup | rg-vc-supply | eastus |
| DF-ENGEMIX-VCBR | rg-analytics-vcbr | eastus |
| DF-MINING-VCBR | rg-analytics-vcbr | brazilsouth |
| DF-BI-INDUSTRIAL-DEV-VCBR | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| DF-NEG-CONTROLADORIA-DEV-VCBR | RG-DATA-controladoria-DEV-VCBR | brazilsouth |
| DF-NEG-CONTROLADORIA-PRD-VCBR | RG-DATA-controladoria-PRD-VCBR | brazilsouth |
| DF-VENDM-INTG-DEV-VCBR | RG-VC-VENDM-QA | brazilsouth |
| DF-VENDM-INTG-PRD-VCBR | RG-VC-VENDM-PRD | brazilsouth |
| DF-NEG-ENGEMIX-DEV-VCBR | RG-DATA-ENGEMIX-DEV-VCBR | brazilsouth |
| DF-NEG-ENGEMIX-PRD-VCBR | RG-DATA-ENGEMIX-PRD-VCBR | brazilsouth |
| DF-BATEULEVOU-QA-VCBR | RG-BATEULEVOU-VCBR-QA | brazilsouth |
| df-taticadodia-qa-vcbr | RG-VC-TATICADODIA-QA | brazilsouth |
| df-bateulevou-prd-vcbr | RG-BATEULEVOU-VCBR-PRD | brazilsouth |
| df-taticadodia-prd-vcbr | RG-VC-TATICADODIA-PRD | brazilsouth |
| DF-MANUTENCAO-DEV-VCBR | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| DF-CORPORATIVO-DEV-VCBR | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| DF-CORPORATIVO-VCBR | RG-ANALYTICS-VCBR | brazilsouth |
| DF-MANUTENCAO-VCBR | RG-ANALYTICS-VCBR | brazilsouth |
| DF-NEG-INDUSTRIAL-DEV-VCBR | RG-DATA-INDUSTRIAL-DEV-VCBR | brazilsouth |
| DF-NEG-INDUSTRIAL-VCBR | RG-DATA-INDUSTRIAL-PRD-VCBR | brazilsouth |
| DF-NEG-LOGISTICA-DEV-VCBR | RG-DATA-LOGISTICA-DEV-VCBR | brazilsouth |
| DF-NEG-LOGISTICA-PRD-VCBR | RG-DATA-LOGISTICA-PRD-VCBR | brazilsouth |
| DF-VCMINING | rg-mining | eastus |


## Lista de Key Vaults

```powershell
az resource list --resource-type "Microsoft.KeyVault/vaults" \
  --query "[].[name, resourceGroup, location]" \
  --output tsv | awk 'BEGIN {print "| Nome | RG | Região |"; print "|---|---|---|"} {print "| "$1" | "$2" | "$3" |"}'
  ```

| Nome | RG | Região |
|---|---|---|
| kvdatascivcbr | rg-datasci-vcbr | northcentralus |
| kv-controlador-prd-vcbr | RG-DATA-CONTROLADORIA-PRD-VCBR | brazilsouth |
| kv-controlador-dev-vcbr | RG-DATA-controladoria-DEV-VCBR | brazilsouth |
| KV-ANALYTICS-VCBR | RG-ANALYTICS-VCBR | brazilsouth |
| KV-DEV-ANALYTICS-VCBR | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| akvvcasup | RG-VC-SUPPLY | brazilsouth |
| kv-comercial-dev-vcbr | RG-DATA-COMERCIAL-DEV-VCBR | brazilsouth |
| kv-comercial-prd-vcbr | RG-DATA-COMERCIAL-PRD-VCBR | brazilsouth |
| KV-VENDM-DEV-VCBR | RG-VC-VENDM-QA | brazilsouth |
| KV-VENDM-PRD-VCBR | RG-VC-VENDM-PRD | brazilsouth |
| kv-data-portal-qa-vcbr | RG-DATA-PORTAL-QA | brazilsouth |
| kv-engemix-dev-vcbr | RG-DATA-ENGEMIX-DEV-VCBR | brazilsouth |
| kv-engemix-prd-vcbr | RG-DATA-ENGEMIX-PRD-VCBR | brazilsouth |
| kv-data-portal-prod-vcbr | RG-DATA-PORTAL-PRD | brazilsouth |
| kv-automacao-eo-prd-vcbr | RG-AUTOMACAO-CRC-VCBR-PRD | brazilsouth |
| KV-HUBSERVICOS-QA-VCBR | RG-HUBSERVICOS-QA-VCBR | brazilsouth |
| KV-CERTIFICADO-VCBR | RG-TI-VCBR | brazilsouth |
| kvsscvcna | RG-VC-SUPPLY | eastus |
| kv-industrial-prd-vcbr | RG-DATA-INDUSTRIAL-PRD-VCBR | brazilsouth |
| kv-industrial-dev-vcbr | RG-DATA-INDUSTRIAL-DEV-VCBR | brazilsouth |
| vc-lab-keyvault | RG-VC-AISERVICES-LAB | brazilsouth |
| kv-vendm-qa-vcbr | RG-VC-VENDM-QA | brazilsouth |
| KV-TRANSC-AUD-DEV-VCBR | RG-VC-TRANSCRICAO-AUDIO-COMERCIAL-DEV | brazilsouth |
| KV-COM-DIGITAL-DEV-VCBR | RG-VC-AI-COMERCIAL-VCBR | brazilsouth |
| KV-IA-GEN-IND-DEV-VCBR | RG-VC-IA-PROCESSOS-INDUSTRIAIS-DEV | brazilsouth |
| KV-DEV-CONSULT-VIRT-VCBR | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| KV-LOGISTICA-DEV-VCBR | RG-DATA-LOGISTICA-DEV-VCBR | brazilsouth |
| kv-ges-refrat-dev-vcbr | RG-VC-GESTAO-REFRATARIOS-DEV | brazilsouth |
| kv-ges-refrat-qa-vcbr | RG-VC-GESTAO-REFRATARIOS-QA | brazilsouth |
| KV-LOGISTICA-PRD-VCBR | RG-DATA-LOGISTICA-PRD-VCBR | brazilsouth |
| KV-LOGISTICAVC-QA-VCBR | RG-VC-LOGISTICAVC-QA | brazilsouth |
| KV-M-AVULSAS-TI-QA-VCBR | RG-VC-MANUTENCOES-AVULSAS-TI-QA | brazilsouth |
| KV-M-AVULSAS-TI-PRD-VCBR | RG-VC-MANUTENCOES-AVULSAS-TI-PRD | brazilsouth |
| kv-ges-refrat-prd-vcbr | RG-VC-GESTAO-REFRATARIOS-PRD | brazilsouth |
| KV-LOGISTICAVC-PRD-VCBR | RG-VC-LOGISTICAVC-PRD | brazilsouth |
| kv-vloz2-qa | RG-VC-VLOZ2-QA | brazilsouth |
| kv-vloz2-prd | RG-VC-VLOZ2-PRD | brazilsouth |
| KV-AUTH-QA-VCBR | RG-HUBSERVICOS-QA-VCBR | brazilsouth |
| KV-REGISTRATION-QA-VCBR | RG-HUBSERVICOS-QA-VCBR | brazilsouth |
| KV-MARK-QA-VCBR | RG-HUBSERVICOS-QA-VCBR | brazilsouth |
| KV-CARREGAMENTO-QA-VCBR | RG-HUBSERVICOS-QA-VCBR | brazilsouth |
| KV-INTEGRATION-QA-VCBR | RG-HUBSERVICOS-QA-VCBR | brazilsouth |
| KV-AUTH-PRD-VCBR | RG-HUBSERVICOS-PRD-VCBR | brazilsouth |
| KV-CARREGAMENTO-PRD-VCBR | RG-HUBSERVICOS-PRD-VCBR | brazilsouth |
| KV-INTEGRATION-PRD-VCBR | RG-HUBSERVICOS-PRD-VCBR | brazilsouth |
| KV-MARK-PRD-VCBR | RG-HUBSERVICOS-PRD-VCBR | brazilsouth |
| KV-REGISTRATION-PRD-VCBR | RG-HUBSERVICOS-PRD-VCBR | brazilsouth |
| KV-AISERVICE-LAB-VCBR | RG-VC-AISERVICES-LAB | brazilsouth |
| KV-AISERVICE-PRD-VCBR | RG-VC-AISERVICES-PRD | brazilsouth |
| KV-VCMINING-QA-VCBR | RG-VC-VCMINING-QA | brazilsouth |
| KV-VCMINING-PRD-VCBR | RG-VC-VCMINING-PRD | brazilsouth |
| KV-NVALEADS-QA-VCBR | RG-VC-NVALEADS-QA | brazilsouth |
| KV-NVALEADS-PRD-VCBR | RG-VC-NVALEADS-PRD | brazilsouth |
| KV-AZURELOCAL-PRD-VCBR | RG-VC-AZURELOCAL-PRD | eastus |
| Migration-VC5875kv | RG-VC-AZURELOCAL-PRD | centralus |

## Lista de Storage Accounts

```powershell
az resource list --resource-type "Microsoft.Storage/storageAccounts" \
  --query "[].[name, resourceGroup, location]" \
  --output tsv | awk 'BEGIN {print "| Nome | RG | Região |"; print "|---|---|---|"} {print "| "$1" | "$2" | "$3" |"}'
  ```

| Nome | RG | Região |
|---|---|---|
| sacomercialdevvcbr | RG-DATA-COMERCIAL-DEV-VCBR | brazilsouth |
| sacomercialprdvcbr | RG-DATA-COMERCIAL-PRD-VCBR | brazilsouth |
| sadatadiskiso | rg-appmotorista-vcbr | brazilsouth |
| savcpcn | RG-VC-PCN | brazilsouth |
| sahubvendasbeqa | RG-VC-VENDM-QA | brazilsouth |
| sadatalakevcbr | RG-ANALYTICS-VCBR | brazilsouth |
| sasynanalyticsvcbr | rg-analytics-vcbr | brazilsouth |
| sadatalakedevvcbr | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| dbstoraget6a3mnolis526 | databricks-rg-dbr-dataeng02-vcbr-yxbggaakfnilu | brazilsouth |
| dbstoragefjvakyo7xndgw | databricks-rg-dbr-dataeng01-vcbr-ij2yacy4klzko | brazilsouth |
| sacontroladoriadevvcbr | RG-DATA-controladoria-DEV-VCBR | brazilsouth |
| dbstoragepnpzya4niuqj6 | databricks-rg-dbr-dataeng03-vcbr-wnufhckyosrqk | brazilsouth |
| aksanalytics01vcbr | rg-analytics-vcbr | brazilsouth |
| saafopercim1prdvcbr | RG-ANALYTICS-VCBR | brazilsouth |
| sqlva6su3adcghhhlm | rg-analytics-dev-vcbr | brazilsouth |
| dbstoragee7776gcpqghpk | databricks-rg-dbr-datasci-vcbr-w55i5qzzufaek | brazilsouth |
| dbstoragewaw62vaupkwz2 | databricks-rg-dbr-dataeng01-dev-vcbr-uqsnxqnd5udyi | brazilsouth |
| sadatascisandbox | rg-datasci-vcbr | brazilsouth |
| savcdatabricksunitcatbr | RG-ANALYTICS-VCBR | brazilsouth |
| dbstorage5r3moybnzf4li | databricks-rg-dbr-dataeng03-dev-vcbr-ks7hah27e5zo4 | brazilsouth |
| storageaccountrganab274 | rg-analytics-vcbr | brazilsouth |
| saafopercim1devvcbr | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| saaflistdevanalyticsvcbr | rg-analytics-dev-vcbr | brazilsouth |
| sadevanalyticsvcbr | rg-analytics-dev-vcbr | brazilsouth |
| dbstorageuprh52pxjukl4 | databricks-rg-dbr-dataeng02-dev-vcbr-eqkcsoho4ia7a | brazilsouth |
| sasynanalyticsqasvcbr | rg-analytics-dev-vcbr | brazilsouth |
| saprodanalyticsvcbr | rg-analytics-vcbr | brazilsouth |
| sacontroladoriaprdvcbr | RG-DATA-controladoria-PRD-VCBR | brazilsouth |
| sasynanalyticsdevvcbr | rg-analytics-dev-vcbr | brazilsouth |
| dbstoragerzdgxcefp2qhw | databricks-rg-dbvcasup-iy2pqyxlhskzm | brazilsouth |
| rgappmotoristavcbr849 | rg-appmotorista-vcbr | brazilsouth |
| stvcibpprd | RG-IBP-PRD-VCBR | brazilsouth |
| sasqlcrc | rg-sqlcrc-blobstorage | eastus |
| sao365vc | rg-ti-vcbr | brazilsouth |
| stvcibpqa | rg-ibp-qa-vcbr | brazilsouth |
| saappvc | rg-appmotorista-vcbr | brazilsouth |
| salogs01 | rg-storage | brazilsouth |
| sagpatioqa | rg-hubservicos-qa-vcbr | brazilsouth |
| sabivcasup | rg-vc-supply | brazilsouth |
| rgappmotoristavcbr783 | rg-appmotorista-vcbr | brazilsouth |
| rgibpqavcbrad0c | rg-ibp-qa-vcbr | brazilsouth |
| rgibpprdvcbr89b8 | rg-ibp-prd-vcbr | brazilsouth |
| stvcsimuladorprd | RG-VC-SIMULADOR-PRD | brazilsouth |
| stvcsimuladorqa | rg-vc-simulador-qa | brazilsouth |
| savcnetskopeprd | RG-NETSKOPE-VCBR | brazilsouth |
| savcvlozqa | RG-VC-VLOZ-QA | brazilsouth |
| savcvlozprd | RG-VC-VLOZ-PRD | brazilsouth |
| savlozextratobeprd | RG-VC-VLOZ-PRD | brazilsouth |
| dbstoragenea3cuesokga6 | rg-dbr-consulta-vcbr | brazilsouth |
| savlozextratobeqa | RG-VC-VLOZ-QA | brazilsouth |
| sagpatioprod | rg-hubservicos-prd-vcbr | brazilsouth |
| saafvlozvcbr | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| sabkpgpatio | rg-hubservicos-prd-vcbr | brazilsouth |
| dbstorageq5saxuyemv6ke | rg-dbr-neg-comercial-dev-vcbr-gmevhcmqbnpqs | brazilsouth |
| dbstorageweknjn7kvaezo | rg-dbr-neg-comercial-prd-vcbr-z2lw7eal4bnfs | brazilsouth |
| dbstorage5z2xwotmsih76 | rg-dbr-neg-controladoria-dev-vcbr-kmrwbnorej7fg | brazilsouth |
| dbstorage3juam4jfxxn5o | rg-dbr-neg-controladoria-prd-vcbr-7oqowkuqtzkra | brazilsouth |
| sahubvendasbeprd | RG-VC-VENDM-PRD | brazilsouth |
| saengemixdevvcbr | RG-DATA-ENGEMIX-DEV-VCBR | brazilsouth |
| dbstorage7plupykyvzvl4 | rg-dbr-neg-engemix-dev-vcbr-xhwblc7npn7ke | brazilsouth |
| saengemixprdvcbr | RG-DATA-ENGEMIX-PRD-VCBR | brazilsouth |
| dbstoragehlzzqc5odywb4 | rg-dbr-neg-engemix-prd-vcbr-q2mb4czrkdrqw | brazilsouth |
| sabateulevouqavcbr | RG-BATEULEVOU-VCBR-QA | brazilsouth |
| sataticadodiaqavcbr | RG-VC-TATICADODIA-QA | brazilsouth |
| sabateulevouprdvcbr | RG-BATEULEVOU-VCBR-PRD | brazilsouth |
| sataticadodiaprdvcbr | RG-VC-TATICADODIA-PRD | brazilsouth |
| saaiserviceslabvcbr | RG-VC-AISERVICES-LAB | brazilsouth |
| safilesynclabvcbr | RG-VC-FILESYNC-LAB | brazilsouth |
| salogssecurityprdvcbr | RG-Security-Policies-VC | brazilsouth |
| safamanutencaovcbr | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| safugaplacaprdvcbr | RG-VC-FUGAPLACA-PRD | brazilsouth |
| sagtriscosprdvcbr | RG-VC-GESTAODERISCOS-PRD | brazilsouth |
| saindustrialdevvcbr | RG-DATA-INDUSTRIAL-DEV-VCBR | brazilsouth |
| saindustrialvcbr | RG-DATA-INDUSTRIAL-PRD-VCBR | brazilsouth |
| safamanutencaoprdvcbr | RG-ANALYTICS-VCBR | brazilsouth |
| dbstorageq7pfaedzlaggq | databricks-rg-dbr-neg-industrial-dev-vcbr-25nqujofud7cy | brazilsouth |
| dbstoragegvbevcbffl25s | databricks-rg-dbr-neg-industrial-prd-vcbr-ncalyibyqnwo4 | brazilsouth |
| stfuncvcbrv02025 | RG-VC-AISERVICES-LAB | brazilsouth |
| satranscricaoauddevvcbr | RG-VC-TRANSCRICAO-AUDIO-COMERCIAL-DEV | brazilsouth |
| saiagenindustrialdevvcbr | RG-VC-IA-PROCESSOS-INDUSTRIAIS-DEV | brazilsouth |
| dbstoragedlumor3rd2ts2 | RG-DBR-DATAENG04-DEV-VCBR | brazilsouth |
| dbstoragewpz4pbteepwcy | RG-DBR-DATAENG04-VCBR | brazilsouth |
| sacrcbotmtxgranelprdvcbr | RG-AUTOMACAO-CRC-VCBR-PRD | brazilsouth |
| salogssqlgpatiovcbr | RG-GESTAODEPATIO-DB-VCBR | brazilsouth |
| saconsultorvirtualdvvcbr | RG-ANALYTICS-DEV-VCBR | brazilsouth |
| dbstoragekcwkzb7j7krau | RG-DBR-AISERVICES-VCBR-MANAGED | brazilsouth |
| savcasup | RG-VC-SUPRIMENTOS-PRD | brazilsouth |
| salogisticadevvcbr | RG-DATA-LOGISTICA-DEV-VCBR | brazilsouth |
| dbstoragetw5votehjbv62 | databricks-rg-dbr-neg-logistica-dev-vcbr-ohql25dllqbos | brazilsouth |
| safomentodevvcbr | RG-VC-AISERVICES-LAB | brazilsouth |
| saplaygrounddevvcbr | RG-VC-AISERVICES-LAB | brazilsouth |
| saagenttidevvcbr | RG-VC-AISERVICES-LAB | brazilsouth |
| sabkpmining | RG-VC-VCMINING-PRD | brazilsouth |
| sagesrefratdevvcbr | RG-VC-GESTAO-REFRATARIOS-DEV | brazilsouth |
| sagesrefratqavcbr | RG-VC-GESTAO-REFRATARIOS-QA | brazilsouth |
| salogisticaprdvcbr | RG-DATA-LOGISTICA-PRD-VCBR | brazilsouth |
| salogisticavcqavcbr | RG-VC-LOGISTICAVC-QA | brazilsouth |
| dbstorager7zcnmnp3nmya | databricks-rg-dbr-neg-logistica-prd-vcbr-pjlp3imngp6rk | brazilsouth |
| sagesrefratprdvcbr | RG-VC-GESTAO-REFRATARIOS-PRD | brazilsouth |
| salogisticavcprdvcbr | RG-VC-LOGISTICAVC-PRD | brazilsouth |
| bacpacueno | RG-VC-VENDM-QA | brazilsouth |
| dbstoragenffnyaed6ykp4 | RG-DBR-AISERV-DEV-VCBR | brazilsouth |
| dbstoragebornenuogdaiq | RG-DBR-AISERV-PRD-VCBR | brazilsouth |
| sarestorevmlogvc01 | RG-VC-LOGISTICAVC-PRD | brazilsouth |
| savloz2qavcbr | RG-VC-VLOZ2-QA | brazilsouth |
| savloz2prdvcbr | RG-VC-VLOZ2-PRD | brazilsouth |
| sacomercialrestdevvcbr | RG-DATA-COMERCIAL-DEV-VCBR | brazilsouth |
| sacomrestritodevvcbr | RG-DATA-COMERCIAL-DEV-VCBR | brazilsouth |
| sacomrestritoprdvcbr | RG-DATA-COMERCIAL-PRD-VCBR | brazilsouth |
| stgvcaislabpv01 | RG-VC-AISERVICES-LAB | brazilsouth |
| cs210032001f18aebe8 | cloud-shell-storage-eastus | eastus |
| cs210032000ea0530e5 | cloud-shell-storage-eastus | eastus |
| cs2100320007fe055ea | cloud-shell-storage-eastus | eastus |
| brsaowvhcicledd34175a6a0 | RG-VC-AZURELOCAL-PRD | eastus |
| migratersa217539082 | RG-VC-AZURELOCAL-PRD | centralus |


## Lista de Resource Groups

```powershell
az group list --output table
```


| Name                                                             | Location        | Status     |
|------------------------------------------------------------------|-----------------|------------|
| DefaultResourceGroup-CQ                                          | brazilsouth     | Succeeded  |
| RG-BACKUP                                                        | brazilsouth     | Succeeded  |
| AzureBackupRG_brazilsouth_1                                      | brazilsouth     | Succeeded  |
| RG-CREGISTRY-VCBR                                                | brazilsouth     | Succeeded  |
| RG-IBP-QA-VCBR                                                   | brazilsouth     | Succeeded  |
| RG-IBP-PRD-VCBR                                                  | brazilsouth     | Succeeded  |
| RG-TI-VCBR                                                       | brazilsouth     | Succeeded  |
| RG-MONITOR-ALERT                                                 | brazilsouth     | Succeeded  |
| RG-MONITOR-SN                                                    | brazilsouth     | Succeeded  |
| asprganalytics                                                   | brazilsouth     | Succeeded  |
| RG-storage                                                       | brazilsouth     | Succeeded  |
| databricks-rg-dbr-dataeng01-vcbr-ij2yacy4klzko                   | brazilsouth     | Succeeded  |
| databricks-rg-dbr-dataeng03-vcbr-wnufhckyosrqk                   | brazilsouth     | Succeeded  |
| RG-SUPORTEVSA-VCBR                                               | brazilsouth     | Succeeded  |
| databricks-rg-dbr-dataeng02-vcbr-yxbggaakfnilu                   | brazilsouth     | Succeeded  |
| databricks-rg-dbr-dataeng03-dev-vcbr-ks7hah27e5zo4               | brazilsouth     | Succeeded  |
| databricks-rg-dbr-dataeng01-dev-vcbr-uqsnxqnd5udyi               | brazilsouth     | Succeeded  |
| RG-ANALYTICS-DEV-SYNQA-VCBR                                      | brazilsouth     | Succeeded  |
| RG-SYN-CONTROLADORIA-DEV-VCBR                                    | brazilsouth     | Succeeded  |
| databricks-rg-dbr-dataeng02-dev-vcbr-eqkcsoho4ia7a               | brazilsouth     | Succeeded  |
| RG-SYN-CONTROLADORIA-PRD-VCBR                                    | brazilsouth     | Succeeded  |
| RG-DATASCI-VCBR                                                  | brazilsouth     | Succeeded  |
| databricks-rg-dbr-datasci-vcbr-w55i5qzzufaek                     | brazilsouth     | Succeeded  |
| RG-DATA-CONTROLADORIA-PRD-VCBR                                   | brazilsouth     | Succeeded  |
| synapseworkspace-managedrg-83d33459-ed18-4036-8e5d-426949453665  | brazilsouth     | Succeeded  |
| synapseworkspace-managedrg-0297de15-9c95-4e7f-83a0-e406ed15fd89  | brazilsouth     | Succeeded  |
| rg-dbr-consulta-vcbr                                             | brazilsouth     | Succeeded  |
| RG-DATA-COMERCIAL-DEV-VCBR                                       | brazilsouth     | Succeeded  |
| rg-dbr-neg-comercial-prd-vcbr-z2lw7eal4bnfs                      | brazilsouth     | Succeeded  |
| rg-dbr-neg-comercial-dev-vcbr-gmevhcmqbnpqs                      | brazilsouth     | Succeeded  |
| RG-DATA-COMERCIAL-PRD-VCBR                                       | brazilsouth     | Succeeded  |
| rg-dbr-neg-controladoria-dev-vcbr-kmrwbnorej7fg                  | brazilsouth     | Succeeded  |
| rg-dbr-neg-controladoria-prd-vcbr-7oqowkuqtzkra                  | brazilsouth     | Succeeded  |
| RG-ANALYTICS-VCBR                                                | brazilsouth     | Succeeded  |
| RG-NETSKOPE-VCBR                                                 | brazilsouth     | Succeeded  |
| RG-DNS                                                           | brazilsouth     | Succeeded  |
| RG-DATA-CONTROLADORIA-DEV-VCBR                                   | brazilsouth     | Succeeded  |
| RG-APPMOTORISTA-VCBR                                             | brazilsouth     | Succeeded  |
| RG-GESTAODEPATIO-APP-VCBR                                        | brazilsouth     | Succeeded  |
| RG-GESTAODEPATIO-DB-VCBR                                         | brazilsouth     | Succeeded  |
| RG-HUBSERVICOS-QA-VCBR                                           | brazilsouth     | Succeeded  |
| RG-HUBSERVICOS-PRD-VCBR                                          | brazilsouth     | Succeeded  |
| RG-Network                                                       | brazilsouth     | Succeeded  |
| RG-VC-SIMULADOR-PRD                                              | brazilsouth     | Succeeded  |
| RG-VC-PCN                                                        | brazilsouth     | Succeeded  |
| RG-VC-HUBSERVICO-MonitoramentoEntregas-PRD                       | brazilsouth     | Succeeded  |
| RG-VC-HUBSERVICO-MonitoramentoEntregas-QA                        | brazilsouth     | Succeeded  |
| RG-VC-SUPPLY                                                     | brazilsouth     | Succeeded  |
| RG-SQLCRC-BlobStorage                                            | brazilsouth     | Succeeded  |
| RG-VC-SIMULADOR-QA                                               | brazilsouth     | Succeeded  |
| databricks-rg-dbvcasup-iy2pqyxlhskzm                             | brazilsouth     | Succeeded  |
| RG-VC-VLOZ-PRD                                                   | brazilsouth     | Succeeded  |
| RG-VC-VLOZ-QA                                                    | brazilsouth     | Succeeded  |
| RG-VC-VENDM-QA                                                   | brazilsouth     | Succeeded  |
| RG-VERDERA-PRD                                                   | brazilsouth     | Succeeded  |
| RG-ANALYTICS-DEV-VCBR                                            | brazilsouth     | Succeeded  |
| RG-DATA-PORTAL-PRD                                               | brazilsouth     | Succeeded  |
| RG-VC-VENDM-PRD                                                  | brazilsouth     | Succeeded  |
| RG-DATA-PORTAL-QA                                                | brazilsouth     | Succeeded  |
| RG-DATA-LAB-VCBR                                                 | brazilsouth     | Succeeded  |
| RG-Security-Policies-VC                                          | brazilsouth     | Succeeded  |
| RG-DATA-ENGEMIX-DEV-VCBR                                         | brazilsouth     | Succeeded  |
| RG-DATA-ENGEMIX-PRD-VCBR                                         | brazilsouth     | Succeeded  |
| rg-dbr-neg-engemix-dev-vcbr-xhwblc7npn7ke                        | brazilsouth     | Succeeded  |
| rg-dbr-neg-engemix-prd-vcbr-q2mb4czrkdrqw                        | brazilsouth     | Succeeded  |
| RG-AUTOMACAO-CRC-VCBR-PRD                                        | brazilsouth     | Succeeded  |
| RG-BATEULEVOU-VCBR-QA                                            | brazilsouth     | Succeeded  |
| RG-BATEULEVOU-VCBR-PRD                                           | brazilsouth     | Succeeded  |
| RG-VC-TATICADODIA-QA                                             | brazilsouth     | Succeeded  |
| RG-VC-TATICADODIA-PRD                                            | brazilsouth     | Succeeded  |
| RG-VC-AISERVICES-LAB                                             | brazilsouth     | Succeeded  |
| RG-SALOGS                                                        | brazilsouth     | Succeeded  |
| RG-VC-FILESYNC-LAB                                               | brazilsouth     | Succeeded  |
| RG-VC-QCDI-QA                                                    | brazilsouth     | Succeeded  |
| RG-VC-FUGAPLACA-PRD                                              | brazilsouth     | Succeeded  |
| RG-VC-GESTAODERISCOS-PRD                                         | brazilsouth     | Succeeded  |
| RG-VC-MIAPPS-PRD                                                 | brazilsouth     | Succeeded  |
| VisualStudioOnline-D9DCBA7DFBDE483D9C825D06688CF86B              | brazilsouth     | Succeeded  |
| RG-VC-VCMINING-QA                                                | brazilsouth     | Succeeded  |
| RG-VC-COPILOT-PRD                                                | brazilsouth     | Succeeded  |
| RG-DATA-INDUSTRIAL-DEV-VCBR                                      | brazilsouth     | Succeeded  |
| RG-DATA-INDUSTRIAL-PRD-VCBR                                      | brazilsouth     | Succeeded  |
| RG-VC-RPA-PP-PRD                                                 | brazilsouth     | Succeeded  |
| RG-VC-SIMULADOR-DEV                                              | brazilsouth     | Succeeded  |
| RG-VC-SRDE-IASISTEMACAL-DEV                                      | brazilsouth     | Succeeded  |
| RG-VC-VCMINING-PRD                                               | brazilsouth     | Succeeded  |
| RG-VC-INTUNE-PRD                                                 | brazilsouth     | Succeeded  |
| databricks-rg-dbr-neg-industrial-dev-vcbr-25nqujofud7cy          | brazilsouth     | Succeeded  |
| databricks-rg-dbr-neg-industrial-prd-vcbr-ncalyibyqnwo4          | brazilsouth     | Succeeded  |
| RG-VC-CELULAGRANELEI-DEV                                         | brazilsouth     | Succeeded  |
| RG-VC-TRANSCRICAO-AUDIO-COMERCIAL-DEV                            | brazilsouth     | Succeeded  |
| RG-VC-AI-COMERCIAL-VCBR                                          | brazilsouth     | Succeeded  |
| RG-VC-IA-PROCESSOS-INDUSTRIAIS-DEV                               | brazilsouth     | Succeeded  |
| RG-DBR-DATAENG04-DEV-VCBR                                        | brazilsouth     | Succeeded  |
| RG-DBR-DATAENG04-VCBR                                            | brazilsouth     | Succeeded  |
| RG-VC-SUPRIMENTOS-PRD                                            | brazilsouth     | Succeeded  |
| RG-VC-WLC-FORTINET-PRD                                           | brazilsouth     | Succeeded  |
| RG-VC-BOTTEAMS-JURIDICO-PRD                                      | brazilsouth     | Succeeded  |
| RG-DBR-AISERVICES-VCBR-MANAGED                                   | brazilsouth     | Succeeded  |
| RG-DATA-LOGISTICA-DEV-VCBR                                       | brazilsouth     | Succeeded  |
| MA_defaultazuremonitorworkspace-cq_brazilsouth_managed           | brazilsouth     | Succeeded  |
| databricks-rg-dbr-neg-logistica-dev-vcbr-ohql25dllqbos           | brazilsouth     | Succeeded  |
| RG-VC-MANUTENCOES-AVULSAS-TI-QA                                  | brazilsouth     | Succeeded  |
| RG-VC-MANUTENCOES-AVULSAS-TI-PRD                                 | brazilsouth     | Succeeded  |
| RG-VC-GESTAO-DOCUMENTAL-PRD                                      | brazilsouth     | Succeeded  |
| RG-VC-GESTAO-REFRATARIOS-DEV                                     | brazilsouth     | Succeeded  |
| RG-VC-GESTAO-REFRATARIOS-QA                                      | brazilsouth     | Succeeded  |
| RG-DATA-LOGISTICA-PRD-VCBR                                       | brazilsouth     | Succeeded  |
| RG-VC-FABRIC-DIGITAL-PRD                                         | brazilsouth     | Succeeded  |
| RG-VC-LOGISTICAVC-QA                                             | brazilsouth     | Succeeded  |
| RG-VC-SIMULADOR-VESPA-TAC-QA                                     | brazilsouth     | Succeeded  |
| databricks-rg-dbr-neg-logistica-prd-vcbr-pjlp3imngp6rk           | brazilsouth     | Succeeded  |
| RG-VC-AZURELOCAL-AVD-TST                                         | brazilsouth     | Succeeded  |
| RG-VC-GESTAO-REFRATARIOS-PRD                                     | brazilsouth     | Succeeded  |
| RG-VC-LOGISTICAVC-PRD                                            | brazilsouth     | Succeeded  |
| RG-VC-SIMULADOR-VESPA-TAC-PRD                                    | brazilsouth     | Succeeded  |
| RG-VC-AISERVICES-PRD                                             | brazilsouth     | Succeeded  |
| RG-DBR-AISERV-DEV-VCBR                                           | brazilsouth     | Succeeded  |
| RG-DBR-AISERV-PRD-VCBR                                           | brazilsouth     | Succeeded  |
| RG-VC-VLOZ2-QA                                                   | brazilsouth     | Succeeded  |
| RG-VC-VLOZ2-PRD                                                  | brazilsouth     | Succeeded  |
| RG-VC-SITES-PRD                                                  | brazilsouth     | Succeeded  |
| RG-VC-VSATELLITE-PRD                                             | brazilsouth     | Succeeded  |
| RG-VC-CONTROLE-PE-DE-TI-PRD                                      | brazilsouth     | Succeeded  |
| RG-VC-CONTROLE-PE-DE-TI-QA                                       | brazilsouth     | Succeeded  |
| RG-VC-CONTROLE-PE-DE-TI-DEV                                      | brazilsouth     | Succeeded  |
| RG-VC-PORTAL-CLIENTES-FOB-QA                                     | brazilsouth     | Succeeded  |
| RG-VC-INFRA-PRD                                                  | brazilsouth     | Succeeded  |
| ZonalMove-MC-RG-brazilsouth                                      | brazilsouth     | Succeeded  |
| RG-VCBR-WLC-FORTINET                                             | brazilsouth     | Succeeded  |
| RG-VC-FAC-FORTINET-PRD                                           | brazilsouth     | Succeeded  |
| RG-VC-APPGW-GPATIO-EXTERNO-QA                                    | brazilsouth     | Succeeded  |
| RG-VC-APPGW-GPATIO-EXTERNO-PRD                                   | brazilsouth     | Succeeded  |
| RG-VC-INOVACAO-PRD                                               | brazilsouth     | Succeeded  |
| RG-VC-NVALEADS-PRD                                               | brazilsouth     | Succeeded  |
| RG-VC-NVALEADS-QA                                                | brazilsouth     | Succeeded  |
| rg-testeremover                                                  | brazilsouth     | Succeeded  |
| RG-POC-ASR-VCBR                                                  | brazilsouth     | Succeeded  |
| sql-serverteste123_group                                         | brazilsouth     | Succeeded  |
| LogAnalyticsDefaultResources                                     | eastus          | Succeeded  |
| DefaultResourceGroup-EUS                                         | eastus          | Succeeded  |
| cloud-shell-storage-eastus                                       | eastus          | Succeeded  |
| RG-VC-AZURELOCAL-PRD                                             | eastus          | Succeeded  |
| NetworkWatcherRG                                                 | westus          | Succeeded  |
| RG-SPEECHSTUDIO-VCBR                                             | eastus2         | Succeeded  |
| dashboards                                                       | southcentralus  | Succeeded  |
| RG-ITACAMBA                                                      | southcentralus  | Succeeded  |
| RG-VC-POWERBISCALE-PRD                                           | southcentralus  | Succeeded  |
| RG-MINING                                                        | centralus       | Succeeded  |


# Evidências do Ambiente

## Nosso Databricks Unity Catalog

metastore "VC", grupo de administradores: "GUS-VCNET-DATALAKE-DBR-METASTORE-ADM"


![EVIDENCIA_AMBIENTE_VC_UNITY_CATALOG](images/EVIDENCIA_AMBIENTE_VC_UNITY_CATALOG.png)

## Workspaces Configuration

![EVIDENCIA_AMBIENTE_VC_UNITY_CATALOG_WORKSPACES-01](images/EVIDENCIA_AMBIENTE_VC_UNITY_CATALOG_WORKSPACES-01.png)

![EVIDENCIA_AMBIENTE_VC_UNITY_CATALOG_WORKSPACES-02](images/EVIDENCIA_AMBIENTE_VC_UNITY_CATALOG_WORKSPACES-02.png)

![EVIDENCIA_AMBIENTE_VC_UNITY_CATALOG_WORKSPACES-03](images/EVIDENCIA_AMBIENTE_VC_UNITY_CATALOG_WORKSPACES-03.png)

## Storage Locations

Exemplos de Uso de Storage Locations

![EVIDENCIA_AMBIENTE_VC_storage_LOCATIONS](images/EVIDENCIA_AMBIENTE_VC_storage_LOCATIONS.png)

## Unity Catalog Credentials

![EVIDENCIA_AMBIENTE_VC_UC_CREDENTIALS](images/EVIDENCIA_AMBIENTE_VC_UC_CREDENTIALS.png)

## Nossa governança - Perfis de acesso

Ao contrario do que é dito em alguns documentos, nossos acessos são dados a grupos de rede e os usuários são inseridos nos grupos de rede.

Além disso, nossos acessos são concedidos no Unity Catalog e não no storage como é dito em alguns documentos.


### Perfis de acesso corporativo
Esses acesso são de uso **exclusivo** da TI:

- [Data Lake - Administrador de dados](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/6c7601a5-c2bd-40e4-8c91-383acf08097c) - Admins
- [Data Lake - Cientistas de dados VC](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/625a9ef7-5140-4ede-8cbf-88d0fba1b5a1) - Cientistas de dados internos da TI-VC
- [Data Lake - Cientistas de dados Externos](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Overview/groupId/560416fb-0457-4c4a-9a58-8ce12abf53c8) - Cientistas de dados terceirizados
- [Data Lake - Engenheiros de dados VC](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/207c559e-8be4-47e1-bdf5-e28e6146ab96) - Engenheiros de dados da TI-VC
- [Data Lake - Engenheiros de dados Externos](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/bf8979e7-0a03-44b7-9c75-7ca66ad4aeeb) - Engenheiros de dados TI terceirizados 
- [Data Lake - Sustentacao](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/47eba0c0-0008-4016-aed2-f8916b9b2cdf) - Perfil do time de suporte
- [Data lake - Analista Comercial Sustentação](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/4726a833-554c-4e7d-99be-42359638d4ad) - Perfil do comercial para monitoramento do ADF.
- [GUS-VCNET-DATALAKE-ANALISTA-DEV](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Overview/groupId/c0948fe7-4668-45f5-9742-04e1480a4542/menuId/) - Perfil de analistas da TI que desenvolvem dashboards no PowerBI.
- [GUS-VCNET-DATALAKE-APROVADORES-DEVOPS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/f44526da-35db-466d-b405-d77f06157f61) Aprovadores de Pull requests do corporativo.

Observação: O perfil **"Data Lake - Sustentacao"** não dá acesso às máquinas dos integration runtimes, é necessário solicitar manualmente para Segurança da VC para que a nova pessoa tenha esse acesso a todas as máquinas, lista de máquinas e procedimento em [Suporte Integration Runtimes](https://grupovotorantim.visualstudio.com/VC-ANALYTICS/_wiki/wikis/VC-ANALYTICS.wiki/969/Suporte-Integration-Runtimes).



### Acessos - Business Area
Em cada área de negócios temos um perfil analista, um engenheiro de dados um cientista de dados, esses três perfis dão acesso a desenvolvimentos dentro da business área, eles tem praticamente os mesmos acessos. Foram criados em separado para permitir evolução futura.
Power User permite dar manutenção nos segredos do Key vault e aprovação de pull requests, cada área precisa destacar pelo menos uma pessoa com esse perfil.
Stakeholder - Não tem acesso para desenvolver, ele pode acessar os painéis de atividades e à Wiki do Azure Devops, podendo ser concedido pessoas com atividade de gestão e outros interessados.

Além dos acessos, o desenvolvedor ainda precisa pedir uma licença Azure Devops Basic para poder ver os repositórios GIT do projeto.

Usuários não devem solicitar esses acessos de áreas que não pertencem, os acesso às tabelas devem ser concedidos via grant de Banco de dados aos grupos que já pertencem mediante aprovação da áreas responsável. Esses acessos devem ser solicitados à area de Dígital. 
Acessos também podem ser solicitados via portal.

#### Controladoria

- [GUS-VCNET-DATALAKE-CONTROLADORIA-ANALISTAS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Overview/groupId/a14e9a50-17e3-429f-a2bf-e62e0091a008/menuId/) Perfil de analista da Controladoria.
- [GUS-VCNET-DATALAKE-CONTROLADORIA-POWERUSER](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Overview/groupId/593a7af9-bb68-4892-a08a-3104b19f35f0/menuId/) Perfil de Power User da Controladoria.
- [GUS-VCNET-DATALAKE-CONTROLADORIA-STAKEHOLDER](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Overview/groupId/ba4d644c-0fee-4f2c-b75b-122b6d2b5781/menuId/) Perfil de Stakeholder da Controladoria.
- [GUS-VCNET-DATALAKE-CONTROLADORIA-CDADOS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Overview/groupId/e2f42e47-6468-4635-840b-31bb66738289/menuId/) Perfil de Cientista de dados da Controladoria.

#### Comercial
- [GUS-VCNET-DATALAKE-COMERCIAL-ANALISTAS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Overview/groupId/2173f93e-70bf-41df-b57e-8aabd138330c/menuId/) Perfil de analista do Comercial.
- [GUS-VCNET-DATALAKE-COMERCIAL-POWERUSER](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Overview/groupId/5e296109-3690-4ab9-a12d-d554409ac046/menuId/) Perfil de Power User do Comercial.
- [GUS-VCNET-DATALAKE-COMERCIAL-STAKEHOLDER](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Overview/groupId/1611d394-4261-4e6d-92c2-d9556317130f/menuId/) Perfil de Stakeholders do Comercial.
- [GUS-VCNET-DATALAKE-COMERCIAL-CDADOS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Overview/groupId/1b387570-a3ad-4ad3-b9bd-e0e214fa9d50/menuId/) Perfil de Cientista de dados do Comercial.
- [GUS-VCNET- DATALAKE-COMERCIAL-ENGDADOS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Overview/groupId/62f91647-4191-4b6c-84c9-b0391df56803/menuId/) - Perfil de engenheiro de dados do comercial

#### Engemix

- [GUS-VCNET-DATALAKE-ENGEMIX-ANALISTAS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/70d4ca0a-307a-405a-92c3-61dbd22073c2/menuId/) Analistas Engemix
- [GUS-VCNET-DATALAKE-ENGEMIX-POWERUSER](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/eacd098b-4fdd-4ba0-9de1-5da33397cdfe/menuId/) Power Users Engemix
- [GUS-VCNET-DATALAKE-ENGEMIX-STAKEHOLDER](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/fb919244-0c36-49f0-af10-de469faec028/menuId/) Stakeholders Engemix
- [GUS-VCNET-DATALAKE-ENGEMIX-ENGDADOS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/0b176af7-efed-4631-a666-a86a18cbd925/menuId/) Engenheiros de dados Engemix
- [GUS-VCNET-DATALAKE-ENGEMIX-CDADOS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/5b7e3ad8-634c-4d5c-b86d-bb86a5f16b2e/menuId/) Cientistas de dados Engemix

#### Logística

- [GUS-VCNET-DATALAKE-LOGISTICA-ANALISTAS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/44c180b4-7468-4aea-b41d-0c0672619506/menuId/) - Analistas da Logística 
- [GUS-VCNET-DATALAKE-LOGISTICA-ENGDADOS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/8e723a5c-fefb-45dd-973c-468c6984f53b/menuId/) - Engenheiros de dados da Logística
- [GUS-VCNET-DATALAKE-LOGISTICA-CDADOS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/43ed1bdb-d877-4bca-83ed-6ae8e2216642/menuId/) - Cientistas de dados da Logística
- [GUS-VCNET-DATALAKE-LOGISTICA-POWERUSER](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/942836c4-e1fa-4700-8adc-25f12dc64491/menuId/) - Power User da Logistica
- [GUS-VCNET-DATALAKE-LOGISTICA-STAKEHOLDER](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/38033b79-0fef-4efc-9778-82a980f579c1/menuId/) - Stakeholder da Logística.


### Acessos analistas (negócios sem business area)

Esses acessos não tem verificação de acesso a nivel de linha, deve ser dado apenas a analistas avançados (Power Users) das áreas de negócio.

- [GUS-VCNET-DATALAKE-INDUSTRIAL-ANALISTAS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/7bb0573d-0740-4848-9427-ccd019b794e3/menuId/) - Analistas  do Industrial 
- [GUS-VCNET-DATALAKE-SUPRIMENTOS-ANALISTAS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/fa2a4c7e-2d28-45a8-ae79-a50613aea652/menuId/) - Analistas de Suprimentos  - SEM EFEITO
- [GUS-VCNET-DATALAKE-GENTE-ANALISTAS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/831237fe-a0c5-4c94-a28c-d4a2166742a8/menuId/) - Analistas de gente - SEM EFEITO
- [GUS-VCNET-DATALAKE-MINING-ANALISTAS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/a0dd515c-41f5-49a8-9b9f-a3c57684e9f3/menuId/) - Analistas de Mineração  - SEM EFEITO
- [GUS-VCNET-DATALAKE-SUSTENTAB-ANALISTAS](https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/e32511d8-4ccb-4ea3-a76a-a7ed53b61da7/menuId/) - Analistas de Sustentabilidade - SEM EFEITO 

### Como Solicitar de acesso?

- Usuários do domínio VC [Como Solicitar de acesso ao Data Lake   usuário VC](/Acessos/Como-Solicitar-de-acesso-ao-Data-Lake-%2D-usuário-VC)
- Funcionários de outras empresas do grupo (CoE) e contingência [Como Solicitar de acesso ao Data Lake   Contingência](/Acessos/Como-Solicitar-de-acesso-ao-Data-Lake-%2D-Contingência)
- Licenças para poder acessar os repositórios - [Como solicitar licenças Azure Devops](/Acessos/Como-solicitar-licenças-Azure-Devops)

