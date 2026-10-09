# Atividades de configuração de CI/CD

Este documento registra o processo completo de configuração do CI/CD que atualiza o Git folder do Databricks a partir de um repositório do Azure DevOps, usando Service Principals com Workload Identity Federation.

Atualizado em 28/09/2026.

## 1. Visão geral do processo

O pipeline não faz deploy de artefatos. Ele apenas força o Git folder da workspace a apontar para o commit mais recente da branch esperada, após push ou merge.

Sequência executada:

1. A task AzureCLI@2 autentica a Service Principal pela Service Connection, sem secret, usando Workload Identity Federation.
2. O script obtém um token temporário do Microsoft Entra ID para o recurso Azure Databricks.
3. O script consulta o Repo ID e valida se ele aponta para o repositório esperado.
4. O script executa PATCH na API de Repos para atualizar a branch.
5. O Databricks executa o fetch real no Azure DevOps usando a credencial Git da Service Principal.

Os passos 1 a 4 usam a identidade da Service Principal no Azure. O passo 5 usa uma credencial diferente, a Git credential registrada no workspace. Essa separação é a origem da maioria das falhas descritas na seção de diagnóstico.

Não é necessário armazenar PAT do Databricks nem do Azure DevOps em variável de pipeline.

### Ambientes

| Ambiente | Pipeline | YAML | Service Connection | Branch | Variável repoid |
| --- | --- | --- | --- | --- | --- |
| Desenvolvimento | dbr-dataeng01-dev-vcbr-pos | azure-pipelines-develop-ci.yml | sc-vc-dbx-cicd-dev | Develop | 176868329579652 |
| Produção | dbr-dataeng01-vcbr-platform-prd-ci | azure-pipelines-main-ci.yml | sc-vc-dbx-cicd-prod | main | 3469373710815373 |

### Identidades

| Ambiente | App Registration | Application (client) ID | Service Connection | Resource Group | Workspace |
| --- | --- | --- | --- | --- | --- |
| Desenvolvimento | sp-vc-dbx-cicd-dev | 4b8b631a-a10b-4cbb-9f26-c353b682d1b5 | sc-vc-dbx-cicd-dev | RG-ANALYTICS-DEV-VCBR | dbr-dataeng01-dev-vcbr |
| Produção | sp-vc-dbx-cicd-prod | 36086633-d812-4de6-b175-db57591f111b | sc-vc-dbx-cicd-prod | RG-ANALYTICS-VCBR | dbr-dataeng01-vcbr |

Tenant: 49158df8-04df-4db2-a1ae-ee3949ca3984.

Dev e produção usam identidades diferentes para limitar o impacto de uma permissão configurada de forma incorreta.

## 2. Ordem de implementação

Execute na ordem abaixo. Cada etapa depende da anterior.

1. Criar a Service Connection, que cria a App Registration.
2. Adicionar a Service Principal à organização do Azure DevOps com licença Basic.
3. Incluir a Service Principal no grupo de segurança do projeto.
4. Conceder View project-level information ao grupo.
5. Conceder Read no repositório ao grupo.
6. Adicionar a Service Principal como identidade na workspace Databricks.
7. Criar a credencial federada no App Registration para o issuer do Databricks.
8. Registrar a Git credential no Databricks com provider Microsoft Entra ID.
9. Conceder CAN EDIT no Git folder.
10. Obter o Repo ID.
11. Criar o pipeline, a variável repoid e autorizar a Service Connection.
12. Executar e validar.

## 3. Criação da Service Connection

A Service Connection cria a App Registration e configura a federação entre Azure DevOps e Microsoft Entra ID. Sem ela, o pipeline precisaria de client secret.

### 3.1 Criação direta

Requer permissão para visualizar a assinatura Azure, criar App Registrations no Microsoft Entra ID e criar atribuições de função no escopo selecionado.

1. No Azure DevOps, acesse Project Settings.
2. Entre em Service connections.
3. Selecione New service connection.
4. Escolha Azure Resource Manager.
5. Selecione App Registration (Automatic) como tipo de identidade.
6. Escolha Workload Identity Federation como tipo de credencial.
7. Selecione a assinatura e limite o escopo ao Resource Group do ambiente.
8. Informe o nome da Service Connection correspondente ao ambiente.
9. Não habilite acesso automático para todos os pipelines.
10. Conclua a criação sem gerar client secret.

Se aparecer a mensagem "You don't appear to have an active Azure subscription", verifique no Portal Azure se a conta está no tenant correto e se enxerga a assinatura. Sem acesso, use o processo por chamado.

### 3.2 Criação por chamado

Texto sugerido:

Solicito a criação de duas Azure DevOps Service Connections no projeto VC-ANALYTICS:

- sc-vc-dbx-cicd-dev, App Registration sugerida sp-vc-dbx-cicd-dev, Resource Group RG-ANALYTICS-DEV-VCBR.
- sc-vc-dbx-cicd-prod, App Registration sugerida sp-vc-dbx-cicd-prod, Resource Group RG-ANALYTICS-VCBR.

Configuração solicitada:

- Tipo Azure Resource Manager.
- Identidade App Registration.
- Autenticação Workload Identity Federation.
- Não criar client secret.
- Limitar cada conexão ao respectivo Resource Group.
- Conceder somente as permissões mínimas necessárias.
- Não liberar as conexões para todos os pipelines.
- Autorizar somente o pipeline do ambiente correspondente.

Após a criação, informar o Application (client) ID, o Directory (tenant) ID e confirmar o nome de cada Service Connection.

### 3.3 Autorização do pipeline

1. Acesse Project Settings, Service connections.
2. Abra a Service Connection.
3. Entre em Pipeline permissions.
4. Selecione Add pipeline.
5. Adicione apenas o pipeline do mesmo ambiente.

Alternativa: execute o pipeline e selecione Permit quando o Azure DevOps solicitar autorização.

Para produção, configure aprovação manual em Approvals and checks da Service Connection sc-vc-dbx-cicd-prod.

## 4. Licença Basic no Azure DevOps

A Service Principal precisa existir como usuária da organização e consumir uma licença de acesso. Sem licença, ela não autentica nas APIs nem no Git, mesmo com todas as permissões concedidas.

1. Acesse Organization Settings, Users.
2. Selecione Add users.
3. No campo de usuário, informe o nome da App Registration, por exemplo sp-vc-dbx-cicd-dev. Service Principals aparecem com o tipo "aad application".
4. Em Access level, selecione Basic.
5. Em Add to project, selecione VC-ANALYTICS.
6. Conclua.

Stakeholder não funciona. O nível Stakeholder bloqueia acesso a Azure Repos.

Confirme depois em Organization Settings, Users, que a coluna Last Access registra atividade após a primeira execução.

## 5. Permissões no Azure DevOps

Esta é a área que mais gera falha. São três níveis independentes: organização, projeto e repositório. Conceder um não concede os outros.

### 5.1 Grupo de segurança

Centralize as Service Principals em um grupo, para evitar atribuições diretas espalhadas por repositório.

1. Acesse Project Settings, Permissions.
2. Selecione New group.
3. Nome: CICD Service Principals.
4. Descrição: grupo criado para dar acesso aos repositórios às Service Principals de CI/CD.
5. Adicione sp-vc-dbx-cicd-dev e sp-vc-dbx-cicd-prod como membros.

### 5.2 Permissão de projeto

Sem esta permissão a Service Principal autentica, lista projetos pela API, mas não resolve o nome do repositório. O sintoma é HTTP 404 na API de repositórios e TF401019 no log do Databricks, que induz ao diagnóstico errado de falta de acesso ao repo.

1. Acesse Project Settings, Permissions.
2. Abra o grupo CICD Service Principals.
3. Aba Permissions.
4. Defina View project-level information como Allow.

Não conceda nada além disso no nível de projeto.

### 5.3 Permissão de repositório

1. Acesse Project Settings, Repositories.
2. Selecione o repositório dbr-dataeng01-vcbr-platform. Para aplicar a todos, selecione o nó All Repositories.
3. Aba Security.
4. Selecione o grupo CICD Service Principals.
5. Defina Read como Allow.

Read é suficiente. O PATCH da API de Repos executa fetch e checkout, que são operações de leitura. O Databricks nunca escreve de volta no repositório nesse fluxo.

Mantenha em Not set: Contribute, Contribute to pull requests, Create branch, Create tag, Create repository, Delete or disable repository, Edit policies, Force push, Manage notes, Manage permissions, Remove others' locks, Rename repository.

Conceda Contribute apenas se algum job passar a commitar do Databricks para o Azure DevOps, o que não é o caso deste processo.

### 5.4 Permissões do Build Service

Independente das Service Principals, o Build Service do projeto precisa de Read no repositório. Para o pipeline de revisão por IA, precisa também de Contribute to Pull Requests para publicar comentários.

## 6. Configuração no Databricks

### 6.1 Adicionar a Service Principal à workspace

O provisionamento via SCIM não funciona para Service Principals. Cadastre diretamente na workspace.

1. Acesse Settings, Identity and access, Service principals, Manage.
2. Selecione Add service principal.
3. Marque Microsoft Entra ID managed.
4. Informe o Application (client) ID do ambiente.
5. Informe o nome da Service Principal.
6. Selecione o entitlement Workspace access.
7. Selecione Unrestricted cluster creation apenas se jobs da SP precisarem criar clusters.
8. Conclua.

### 6.2 Credencial federada no App Registration

Este passo estabelece a confiança OIDC que permite ao Databricks obter tokens do Azure DevOps em nome da Service Principal, sem token estático.

Colete primeiro os dados no Databricks:

1. Na workspace, acesse Settings, Identity and access, Service principals.
2. Abra a Service Principal do ambiente.
3. Aba Git integration, selecione Add Git credential.
4. No menu Git provider, selecione Azure DevOps Services (Microsoft Entra ID).
5. Copie as informações de Issuer, Type e Value.
6. Não marque "I've done the steps above" nem salve ainda.

Em seguida, no Portal Azure:

1. Abra App Registrations e localize a Service Principal.
2. Em Manage, acesse Certificates and secrets.
3. Abra a aba Federated credentials.
4. Selecione Add credential.
5. Em Federated credential scenario, selecione Other issuer.
6. Preencha Issuer, Type e Value com os valores copiados do Databricks.
7. Em Name, informe um identificador claro.

Valores por ambiente:

| Ambiente | Issuer | Type | Value | Name sugerido |
| --- | --- | --- | --- | --- |
| Desenvolvimento | https://adb-1886204700223023.3.azuredatabricks.net/oidc | Explicit subject identifier | 4b8b631a-a10b-4cbb-9f26-c353b682d1b5 | Databricks_dbr-dataeng01-dev-vcbr_git_credential |
| Produção | https://adb-6114343631149730.10.azuredatabricks.net/oidc | Explicit subject identifier | 36086633-d812-4de6-b175-db57591f111b | Databricks_dbr-dataeng01-vcbr_git_credential |

O Value é o próprio Application (client) ID da Service Principal. O Issuer é o host da workspace com o sufixo /oidc.

Ao final, a App Registration terá duas credenciais federadas: a da Service Connection, criada pelo Azure DevOps, e a do Databricks. As duas coexistem e têm finalidades distintas.

### 6.3 Registrar a Git credential

Volte ao Databricks, na tela que ficou aberta:

1. Marque I've done the steps above.
2. Salve.

Confirme que a credencial gravada usa o provider Microsoft Entra ID. Credenciais antigas com provider azureDevOpsServices armazenam um token de curta duração e param de funcionar em aproximadamente uma hora, já que Service Principal não emite PAT no Azure DevOps. Apague credenciais duplicadas para evitar seleção errada.

Listagem das credenciais existentes:

```bash
curl -s \
  -H "Authorization: Bearer ${access_token}" \
  "${DATABRICKS_HOST}/api/2.0/git-credentials"
```

### 6.4 Permissão no Git folder

1. Abra o Git folder /Workspace/Repos/VC-ANALYTICS/dbr-dataeng01-vcbr-platform.
2. Conceda CAN EDIT à Service Principal.
3. Garanta leitura e travessia na pasta pai VC-ANALYTICS, caso a permissão não seja herdada.

CAN MANAGE só é necessário se a Service Principal precisar alterar permissões do Git folder.

## 7. Obtenção do Repo ID

O Repo ID identifica o Git folder dentro de uma workspace específica. Ele não é o ID do repositório no Azure DevOps e não se repete entre workspaces, mesmo com caminho e URL remota iguais.

### Pela interface

1. Abra o Git folder no workspace.
2. Observe a URL do navegador. O número após /repos/ é o Repo ID.

### Pela API

```bash
DATABRICKS_HOST="https://adb-1886204700223023.3.azuredatabricks.net"

access_token=$(az account get-access-token \
  --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d \
  --query accessToken --output tsv)

curl -s \
  -H "Authorization: Bearer ${access_token}" \
  "${DATABRICKS_HOST}/api/2.0/repos" \
  | python3 -m json.tool
```

A resposta traz id, path, url e branch de cada Git folder visível para a identidade autenticada. Localize o registro cujo path corresponde ao Git folder do ambiente e use o campo id.

Valores atuais:

| Ambiente | Workspace | Caminho do Git folder | Repo ID | Branch |
| --- | --- | --- | --- | --- |
| Desenvolvimento | dbr-dataeng01-dev-vcbr | /Workspace/Repos/VC-ANALYTICS/dbr-dataeng01-vcbr-platform | 176868329579652 | Develop |
| Produção | dbr-dataeng01-vcbr | /Workspace/Repos/VC-ANALYTICS/dbr-dataeng01-vcbr-platform | 3469373710815373 | main |

Nunca reutilize o Repo ID de DEV em PROD.

## 8. Configuração dos pipelines

### 8.1 Desenvolvimento

1. Crie ou edite o pipeline dbr-dataeng01-dev-vcbr-pos.
2. Selecione Existing Azure Pipelines YAML file.
3. Informe /azure-pipelines-develop-ci.yml.
4. Crie a variável repoid com o valor 176868329579652.
5. Autorize o pipeline a usar sc-vc-dbx-cicd-dev.

### 8.2 Produção

1. Crie ou edite o pipeline dbr-dataeng01-vcbr-platform-prd-ci.
2. Selecione Existing Azure Pipelines YAML file.
3. Informe /azure-pipelines-main-ci.yml.
4. Crie a variável repoid com o valor 3469373710815373.
5. Autorize o pipeline a usar sc-vc-dbx-cicd-prod.

O pipeline de produção também valida que uma Pull Request para main tenha origem em Develop.

### 8.3 Variáveis

| Variável | DEV | PROD | Secreta |
| --- | --- | --- | --- |
| repoid | 176868329579652 | 3469373710815373 | Não |

Não configure token, ENV_TOKEN ou GLOBAL_TOKEN. A autenticação vem da Service Connection durante a execução.

ENV_REPOID não deve ser criado como variável no Azure DevOps. É a variável de ambiente usada pelo script Bash, alimentada pela variável repoid no bloco env do YAML.

## 9. Script do pipeline

Arquivo azure-pipelines-develop-ci.yml. Para produção, altere a Service Connection, o host, a branch e o trigger.

```yaml
trigger:
  branches:
    include:
      - Develop

pr: none

variables:
  DATABRICKS_HOST: 'https://adb-1886204700223023.3.azuredatabricks.net'
  REPO_BRANCH: 'Develop'
  EXPECTED_REPO_URL: 'https://grupovotorantim.visualstudio.com/VC-ANALYTICS/_git/dbr-dataeng01-vcbr-platform'
  DEBUG_MODE: 'false'

pool:
  # Imagem fixa: ubuntu-latest migra para Ubuntu 26.04 em 19/10/2026.
  vmImage: ubuntu-24.04

stages:
- stage: DatabricksUpdate
  displayName: 'Atualizar Databricks DEV'
  jobs:
  - job: UpdateJob
    displayName: 'Atualizar Git folder protegido'
    steps:
    - task: AzureCLI@2
      displayName: 'Atualizar Git folder na branch Develop'
      inputs:
        azureSubscription: 'sc-vc-dbx-cicd-dev'
        scriptType: bash
        scriptLocation: inlineScript
        inlineScript: |
          set -euo pipefail

          response_dir=$(mktemp -d)
          trap 'rm -rf "${response_dir}"' EXIT

          curl_request() {
            local request_name="$1"
            local response_file="$2"
            local http_code
            local curl_exit
            shift 2

            if http_code=$(curl --silent --show-error \
              --output "${response_file}" \
              --write-out '%{http_code}' \
              "$@"); then
              curl_exit=0
            else
              curl_exit=$?
            fi

            echo "Chamada: ${request_name}"
            echo "Codigo HTTP: ${http_code:-000}"
            echo "Corpo da resposta:"
            if [ -s "${response_file}" ]; then
              cat "${response_file}"
              echo
            else
              echo "(vazio)"
            fi

            if [ "${curl_exit}" -ne 0 ]; then
              echo "Falha de transporte do curl (codigo ${curl_exit})."
              return "${curl_exit}"
            fi

            if [[ ! "${http_code}" =~ ^2[0-9][0-9]$ ]]; then
              echo "A chamada retornou um status HTTP fora da faixa 2xx."
              return 1
            fi

            return 0
          }

          if [[ ! "${ENV_REPOID}" =~ ^[0-9]+$ ]]; then
            echo "##vso[task.logissue type=error]A variavel repoid nao foi definida ou nao e um Repo ID numerico valido."
            exit 1
          fi

          echo "Obtendo token temporario para a Service Principal..."
          access_token=$(az account get-access-token \
            --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d \
            --query accessToken \
            --output tsv)

          if [ -z "${access_token}" ]; then
            echo "##vso[task.logissue type=error]Nao foi possivel obter o token do Azure Databricks."
            exit 1
          fi

          api_url="${DATABRICKS_HOST%/}/api/2.0/repos/${ENV_REPOID}"

          if [[ "${DEBUG_MODE,,}" == "true" ]]; then
            echo "##[group]Diagnostico Databricks"
            echo "ENV_REPOID=${ENV_REPOID}"
            echo "DATABRICKS_HOST=${DATABRICKS_HOST}"
            echo "REPO_BRANCH=${REPO_BRANCH}"
            echo "EXPECTED_REPO_URL=${EXPECTED_REPO_URL}"
            echo "URL montada=${api_url}"
            echo "Tamanho do token=${#access_token}"

            echo "Identidade autenticada:"
            if ! az account show \
              --query '{name:user.name,type:user.type}' \
              --output json; then
              echo "Nao foi possivel consultar a identidade autenticada."
            fi

            if ! curl_request "Identidade Databricks (SCIM Me)" "${response_dir}/scim-me.json" \
              --request GET "${DATABRICKS_HOST%/}/api/2.0/preview/scim/v2/Me" \
              --header "Accept: application/json" \
              --header "Authorization: Bearer ${access_token}"; then
              echo "A consulta SCIM Me falhou, mas o diagnostico nao e bloqueante."
            fi

            if ! curl_request "Credenciais Git da Service Principal" "${response_dir}/git-credentials.json" \
              --request GET "${DATABRICKS_HOST%/}/api/2.0/git-credentials" \
              --header "Accept: application/json" \
              --header "Authorization: Bearer ${access_token}"; then
              echo "A listagem de credenciais Git falhou, mas o diagnostico nao e bloqueante."
            fi

            if ! curl_request "Git folders visiveis" "${response_dir}/repos.json" \
              --request GET "${DATABRICKS_HOST%/}/api/2.0/repos" \
              --header "Accept: application/json" \
              --header "Authorization: Bearer ${access_token}"; then
              echo "A listagem de Git folders falhou, mas o diagnostico nao e bloqueante."
            fi
            echo "##[endgroup]"
          fi

          echo "Validando o Repo ID antes da atualizacao..."
          repo_response_file="${response_dir}/repo-get.json"
          if ! curl_request "Validacao do Git folder" "${repo_response_file}" \
            --request GET "${api_url}" \
            --header "Accept: application/json" \
            --header "Authorization: Bearer ${access_token}"; then
            echo "##vso[task.logissue type=error]Nao foi possivel validar o Git folder no Databricks."
            exit 1
          fi

          repo_json=$(<"${repo_response_file}")

          actual_url=$(python3 -c 'import json, sys; print(json.load(sys.stdin).get("url", ""))' <<< "${repo_json}")
          current_branch=$(python3 -c 'import json, sys; print(json.load(sys.stdin).get("branch", ""))' <<< "${repo_json}")

          if [ "${actual_url%/}" != "${EXPECTED_REPO_URL%/}" ]; then
            echo "##vso[task.logissue type=error]O Repo ID aponta para '${actual_url}', mas era esperado '${EXPECTED_REPO_URL}'."
            exit 1
          fi

          echo "Repositorio validado: ${actual_url}"
          echo "Branch atual: ${current_branch:-nao informada}"
          echo "Atualizando para a branch ${REPO_BRANCH}..."

          payload=$(printf '{"branch":"%s"}' "${REPO_BRANCH}")

          if ! curl_request "Atualizacao da branch do Git folder" "${response_dir}/repo-patch.json" \
            --request PATCH "${api_url}" \
            --header "Content-Type: application/json" \
            --header "Accept: application/json" \
            --header "Authorization: Bearer ${access_token}" \
            --data "${payload}"; then
            echo "##vso[task.logissue type=error]Nao foi possivel atualizar a branch do Git folder no Databricks."
            exit 1
          fi

          echo
          echo "Git folder DEV atualizado com sucesso."
      env:
        ENV_REPOID: $(repoid)
        DATABRICKS_HOST: $(DATABRICKS_HOST)
        REPO_BRANCH: $(REPO_BRANCH)
        EXPECTED_REPO_URL: $(EXPECTED_REPO_URL)
        DEBUG_MODE: $(DEBUG_MODE)
```

Observações sobre o script:

- O GUID 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d é o resource fixo do Azure Databricks no Microsoft Entra ID. Não é específico da VC.
- A validação de EXPECTED_REPO_URL evita atualizar o Git folder errado caso o repoid seja trocado por engano.
- O bloco DEBUG_MODE não é bloqueante e imprime identidade, credenciais Git e Git folders visíveis. Ative apenas durante investigação.
- O script nunca imprime o token, apenas o tamanho.

## 10. Validação da identidade

Teste a Service Principal fora do pipeline quando precisar isolar a causa de uma falha.

### 10.1 Autenticar como a Service Principal

Use um diretório de configuração separado para não sobrescrever o login pessoal. No PowerShell:

```powershell
$env:AZURE_CONFIG_DIR = Join-Path $env:USERPROFILE ".azure-sp"
New-Item -ItemType Directory -Path $env:AZURE_CONFIG_DIR -Force | Out-Null

az login --service-principal `
  -u 4b8b631a-a10b-4cbb-9f26-c353b682d1b5 `
  -p $secret `
  --tenant 49158df8-04df-4db2-a1ae-ee3949ca3984 `
  --allow-no-subscriptions | Out-Null

az account show --query "user" -o json
```

O retorno precisa trazer type igual a servicePrincipal. Se trouxer user, o contexto está errado e o teste não vale.

### 10.2 Testar acesso ao repositório

```powershell
$adoToken = az account get-access-token `
  --resource 499b84ac-1321-427f-aa17-267ca6975798 `
  --query accessToken -o tsv

$adoToken.Length

curl.exe -s -o NUL -w "%{http_code}`n" `
  -H "Authorization: Bearer $adoToken" `
  "https://dev.azure.com/grupovotorantim/VC-ANALYTICS/_apis/git/repositories/dbr-dataeng01-vcbr-platform?api-version=7.1"
```

O GUID 499b84ac-1321-427f-aa17-267ca6975798 é o resource fixo do Azure DevOps.

No PowerShell, chame curl.exe. O comando curl é alias de Invoke-WebRequest e descarta esses parâmetros.

Interpretação do código retornado:

| Código | Significado |
| --- | --- |
| 200 | A Service Principal enxerga o repositório. Permissões no Azure DevOps estão corretas. |
| 302 | Token ausente ou inválido. Redirect para a página de login. |
| 401 | Token emitido para o recurso errado. |
| 404 | Identidade autenticada sem visibilidade do recurso. Falta licença Basic, View project-level information ou Read no repositório. |

### 10.3 Testar escopo por camada

Quando o teste anterior retorna 404, separe projeto de repositório:

```powershell
curl.exe -s -o NUL -w "%{http_code}`n" -H "Authorization: Bearer $adoToken" `
  "https://dev.azure.com/grupovotorantim/_apis/projects?api-version=7.1"

curl.exe -s -H "Authorization: Bearer $adoToken" `
  "https://dev.azure.com/grupovotorantim/VC-ANALYTICS/_apis/git/repositories?api-version=7.1" |
  ConvertFrom-Json | Select-Object -ExpandProperty count
```

Projetos em 200 com count de repositórios igual a zero indica falta de View project-level information ou de Read no repositório.

### 10.4 Encerrar o teste

```powershell
az account clear
```

Feche a janela e remova o client secret criado no App Registration. A operação normal usa federação e não precisa de secret.

## 11. Políticas de branch

Configure em Repos, Branches, Branch policies:

- Develop: adicionar o pipeline de revisão por IA como Build Validation, opcional enquanto estiver em validação.
- main: adicionar o pipeline de produção como Build Validation.
- Bloquear commits diretos em main.
- Permitir promoção para main somente a partir de Develop.

## 12. Pipeline de revisão por IA

O pipeline dbr-dataeng01-dev-vcbr-pre, definido em azure-pipelines-develop-ci-pre.yml, não usa a Service Connection do Databricks.

| Variável | Conteúdo | Secreta |
| --- | --- | --- |
| foundryEndpoint | https://afeu2-ailab-vcdigital-dev-vcbr.services.ai.azure.com/openai/v1 | Não |
| foundryApiKey | Chave da API do Azure AI Foundry | Sim |
| foundryModel | gpt-5.3-codex | Não |

O arquivo .azuredevops/review-prompt.md deve estar versionado.

## 13. Diagnóstico de falhas

| Sintoma | Causa provável | Verificação |
| --- | --- | --- |
| You don't appear to have an active Azure subscription | Tenant incorreto ou usuário sem acesso à assinatura | Confirme o tenant no Portal Azure |
| Erro 401 na API do Databricks | Service Principal não adicionada à workspace ou token emitido para recurso incorreto | Confira o resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d e a lista de Service principals da workspace |
| Erro 403 na API do Databricks | Falta de permissão no Git folder ou na workspace | Confirme CAN EDIT no Git folder |
| Erro 404 na API do Databricks | repoid pertence a outra workspace ou host incorreto | Liste /api/2.0/repos e compare o id |
| GET do Git folder retorna 200 e PATCH retorna 400 | O GET lê metadados no control plane, o PATCH executa fetch real no Azure DevOps | O problema está na credencial Git ou nas permissões do Azure DevOps, não no token do Databricks |
| TF401019 com COULD_NOT_READ_REMOTE_REPOSITORY | Service Principal sem visibilidade do repositório | Verifique licença Basic, View project-level information e Read no repositório |
| Falha após aproximadamente uma hora da configuração | Git credential com provider azureDevOpsServices guardando token de curta duração | Migre a credencial para Azure DevOps Services (Microsoft Entra ID) |
| selectionMethod ByRepositoryUrl com repoUrl vazio | Credenciais Git duplicadas ou host divergente entre credencial e Git folder | Liste /api/2.0/git-credentials e padronize o host |
| Pipeline informa URL de repositório diferente da esperada | repoid aponta para outro Git folder | Corrija a variável repoid |

Diferença importante: a autenticação usada para chamar a API do Databricks não é a mesma credencial usada pelo Git folder para ler o Azure DevOps. Trate as duas de forma separada durante o diagnóstico.

## 14. Registro de chamados

### RITM1171428, Service Connections

Status atendido. [Consultar o chamado](https://votorantim.service-now.com/vcbr?id=itsm_ticket&table=sc_req_item&sys_id=1b77a6a0976f4f5087a07e66f053af7e)

Criação das Service Connections do projeto VC-ANALYTICS como Azure Resource Manager, usando App Registration e Workload Identity Federation, sem client secret, com escopo limitado ao Resource Group de cada ambiente e privilégio inicial de Reader.

### RITM1172545, Provisionamento das Service Principals

Status não atendido via SCIM. O provisionamento SCIM não suporta Service Principals. As identidades foram cadastradas diretamente no Databricks, conforme a seção 6.1. [Consultar o chamado](https://votorantim.service-now.com/vcbr?id=sc_request&is_new_order=true&table=sc_req_item&sys_id=50b304819723431087a07e66f053afa2)

- Enterprise Application: VC-DATABRICKS-dbr-dataeng01-vcbr-SCIM-PRD.
- Aplicação: Azure Databricks SCIM Provisioning Connector.

### RITM1173539, Credencial Git

[Consultar o chamado](https://votorantim.service-now.com/vcbr?id=sc_request&is_new_order=true&table=sc_req_item&sys_id=50b304819723431087a07e66f053afa2)

A configuração da credencial federada exige acesso de owner ao App Registration para alex.cordeiro.ac1@vcimentos.com, ou execução das etapas da seção 6.2 em seu nome.

## 15. Permissões no Azure

Os pipelines não criam nem alteram recursos pelo Azure Resource Manager. A identidade serve apenas para obter um token temporário e chamar a API do Databricks.

- Limitar a Service Principal ao Resource Group do respectivo ambiente.
- Usar Reader como atribuição inicial, quando uma função Azure for exigida para criar ou validar a Service Connection.
- Não conceder Owner ou Contributor.

Permissão para criar a App Registration ou configurar a federação é necessária apenas para quem prepara a Service Connection, não para a execução diária do pipeline.

## 16. Próximos passos

- Reduzir Contribute, Create branch e Create tag para Not set no grupo CICD Service Principals, mantendo somente Read.
- Confirmar que a credencial Git de cada workspace usa o provider Microsoft Entra ID.
- Remover credenciais Git antigas com provider azureDevOpsServices.
- Confirmar que cada Service Connection está autorizada somente para o pipeline do ambiente correspondente.
- Executar e validar o pipeline dbr-dataeng01-dev-vcbr-pos.
- Configurar aprovação para produção e testar dbr-dataeng01-vcbr-platform-prd-ci.
- Remover a variável secreta antiga token após a validação completa.
- Remover o client secret criado para diagnóstico no App Registration sp-vc-dbx-cicd-dev.
