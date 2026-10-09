# Azure Functions `observability-api`

| Item | Valor |
|---|---|
| Tipo de componente | Azure Functions, plano Flex Consumption, Python 3.11, modelo de programação v2 (decorators) |
| Código | `src/`: `function_app.py` (rotas), `auth.py`, `db.py`, `repository.py`, `resources.py`, `constants.py`, `host.json` |
| Infra | `infra/main.bicep` + `infra/params.json`; esteira própria `azure-pipelines.yml` (zip, sem imagem) |
| Recurso | `AF-OBSERV-{DEV-,}VCBR`, plano `<app>-plan`, Storage `stobserv{dev,}vcbr` |
| Identidade | `mi-observ-api-{dev-,}vcbr` |
| Dependência | Hub operacional em Azure SQL (tabelas `tbl_*` e views `vw_*` citadas abaixo), descrito em `doc/01-hub-azure-sql.md` do repositório `cae-observ-vcbr` |

## 1. Objetivo funcional

Ser a porta de acesso ao hub para pessoas e sistemas: consultar incidentes abertos e recém-fechados e manter as tabelas de configuração (workspaces, rotas de alerta, expectativas, mapeamento de domínio e Self-Hosted Integration Runtimes monitorados) sem que ninguém precise de conexão direta com o banco. Não grava incidentes nem execuções; isso é exclusivo do coletor e do `hub-publisher`, ambos no repositório `cae-observ-vcbr` (`src/observability-collector` e `src/hub-publisher`; documentação em `doc/10-observability-collector.md` e `doc/12-hub-publisher.md`).

## 2. Rotas

Todas exigem `Authorization: Bearer <token>`. Respostas em JSON; listas no formato `{"total": n, "itens": [...]}`; erros no formato `{"erro": "mensagem"}`.

| Método | Rota | Escopo | Fonte | Parâmetros |
|---|---|---|---|---|
| `GET` | `/api/problems` | leitura | `vw_open_problems`, ordenado por severidade e abertura | `domain` (opcional), `limit` (padrão 100, máximo 1000) |
| `GET` | `/api/problems/closed` | leitura | `vw_recently_closed_problems`, ordenado por fechamento decrescente | `domain`, `limit` |
| `GET` | `/api/config/{resource}` | leitura | Tabela do recurso | Qualquer coluna do recurso como filtro de igualdade; `limit` |
| `GET` | `/api/config/{resource}/{key}` | leitura | Uma linha pela chave | Chave composta separada por `~`, na ordem da tabela abaixo |
| `POST` | `/api/config/{resource}` | escrita | `INSERT` | Corpo JSON com as colunas |
| `PATCH` ou `PUT` | `/api/config/{resource}/{key}` | escrita | `UPDATE` parcial (só os campos enviados) | Corpo JSON |
| `DELETE` | `/api/config/{resource}/{key}` | escrita | `DELETE` | — |

| Recurso | Tabela | Chave | Obrigatórios na criação | Somente leitura |
|---|---|---|---|---|
| `workspaces` | `tbl_cfg_workspaces` | `workspace_id` | `workspace_id`, `workspace_name`, `workspace_url` | `synced_at` |
| `alert-routes` | `tbl_cfg_alert_routing` | `domain~severity~channel` | os quatro, mais `destination` | — |
| `expectations` | `tbl_cfg_execution_expectations` | `source~natural_key` | `source`, `natural_key`, `schedule_kind`, `written_by` | `updated_at` |
| `domains` | `tbl_cfg_domain_scope` | `source~scope` | `source`, `scope`, `domain` | — |
| `integration-runtimes` | `tbl_cfg_integration_runtimes` | `data_factory~integration_runtime` | `data_factory`, `integration_runtime`, `expected_nodes` | — |

Códigos de retorno: `200` leitura/atualização, `201` criação, `204` remoção, `400` corpo inválido, campo desconhecido, campo somente leitura, campo definido pela API, valor fora da regra de validação, chave com número errado de partes ou `limit` não numérico, `401` token ausente, malformado ou inválido (ou token não configurado), `404` recurso ou linha inexistente, `409` chave já existe, `500` erro de banco.

## 3. Regras de negócio

Dois tokens, dois escopos: `TOKEN_READ` para `GET` e `TOKEN_WRITE` para `POST`/`PATCH`/`PUT`/`DELETE`; o token de escrita não dá leitura e vice-versa. Sem variável configurada, ninguém entra (falha fechada). A comparação é em tempo constante. Nomes de tabela e coluna vêm de `resources.py`; todo valor vai como parâmetro do `pyodbc`. Campo desconhecido no corpo é rejeitado, nunca ignorado. Só tabelas `tbl_cfg_*` podem ser recurso (há teste que garante).

**Campos que a API define.** Expectativas criadas pela API recebem `config_source = 'MANUAL'`, valor que a própria API grava; o campo é recusado se vier no corpo, tanto na criação quanto na atualização. Com `AUTO` ou `YAML` a linha passaria a ser gerenciada pelo `sync-expectations`, que a sobrescreveria na execução seguinte sem avisar quem cadastrou. O valor fica em `constants.py`, não escrito direto na descrição do recurso.

**Validação de valor.** Além da presença dos campos obrigatórios, cada recurso declara validações por coluna. Em `integration-runtimes`: `data_factory` e `integration_runtime` precisam ser texto não vazio, e `expected_nodes` um inteiro maior ou igual ao mínimo de `constants.py` (hoje 1, mesmo limite da restrição da tabela). Texto como `"2"`, decimal e booleano são recusados, porque a coluna é `INT` e um booleano viraria 1 silenciosamente. A mensagem de erro nomeia a coluna e o limite, para quem opera entender pela resposta o que corrigir.

**Self-Hosted Integration Runtime.** Só o ADF dono do SHIR se cadastra: Linked IR não entra, porque o sinal vem do ADF dono e cadastrar os dois geraria incidente duplicado. IR fora da tabela não é monitorado; `active = 0` tira do monitoramento sem perder o cadastro. O número esperado de nós nunca é fixo no código do coletor — sai desta tabela.

## 4. Integrações

| Com | Como |
|---|---|
| Azure SQL | `pyodbc` com `SQL_CONNECTION_STRING` (montada pelo Bicep com ODBC Driver 18) e token Entra da MI injetado no driver; conexão aberta e fechada por requisição |
| Key Vault | `TOKEN_READ` e `TOKEN_WRITE` como Key Vault references (`@Microsoft.KeyVault(SecretUri=...)`), resolvidas pela MI (`keyVaultReferenceIdentity`). Se a referência não resolver, a API recusa o valor literal e devolve 401 em vez de aceitar um token previsível |
| Rede | VNet integration na subnet `SUBNET-PRIVATE-AF-OBSERV-{DEV,PRD}-VCBR` para alcançar o Private Endpoint do banco |

## 5. Configuração (App Settings)

| Variável | Valor |
|---|---|
| `AZURE_CLIENT_ID` | Client ID da MI |
| `SQL_CONNECTION_STRING` | `Driver={ODBC Driver 18 for SQL Server};Server=tcp:<servidor>.database.windows.net,1433;Database=<banco>;Encrypt=yes;` |
| `TOKEN_READ`, `TOKEN_WRITE` | Referências ao Key Vault (parâmetros `refTokenRead`/`refTokenWrite`) |
| `ENVIRONMENT` | `dev` ou `prod` |
| `AzureWebJobsStorage__accountName` | Nome da Storage Account da Function |
| `AzureWebJobsStorage__credential` | `managedidentity` — sem chave de conta em App Setting |
| `AzureWebJobsStorage__clientId` | Client ID da MI |

## 6. Permissões

A MI precisa das permissões de banco de CRUD em `tbl_cfg_*` — incluindo `tbl_cfg_integration_runtimes` — e de leitura das duas views (`vw_open_problems` e `vw_recently_closed_problems`), de `Get` de segredo no Key Vault (Access Policy — hoje só existe a do coletor) e de `Storage Blob Data Owner` na Storage Account da Function (pacote de implantação e `AzureWebJobsStorage`). A subnet precisa estar delegada a `Microsoft.App/environments` (exigência do Flex Consumption).

## 7. Implantação

A esteira testa, empacota a pasta em zip (sem testes, infra e README), aplica o Bicep e publica com `az functionapp deploy --type zip --build-remote true`, primeiro em dev e, com aprovação, em prod. Antes do primeiro deploy: criar os dois segredos no Key Vault, preencher `vnetResourceGroup`, `refTokenRead` e `refTokenWrite` em `params.json` (hoje com `AJUSTAR`), e corrigir as pendências críticas da seção 9.

A tabela `tbl_cfg_integration_runtimes` precisa existir no hub antes de o recurso `integration-runtimes` ser usado. O DDL é criado pelo `hub-schema-init`, no repositório `cae-observ-vcbr` (`doc/14-hub-schema-init.md`), que também concede as permissões da MI da API nessa tabela.

## 8. Critérios de aceite

`GET /api/problems` sem token devolve 401; com o token de escrita, 401; com o de leitura, 200 e a lista de incidentes abertos. `POST /api/config/alert-routes` com o token de escrita cria a rota e um segundo `POST` igual devolve 409. `GET /api/config/expectations?source=ADF` filtra. Um corpo com campo inexistente devolve 400. `POST /api/config/integration-runtimes` cadastra o IR e um `PATCH` com `active: 0` tira do monitoramento; `expected_nodes: 0` devolve 400 com mensagem que nomeia a coluna e o mínimo. `POST /api/config/expectations` com `config_source` no corpo devolve 400, e sem o campo cria a linha com `MANUAL`. Os testes unitários de `src/tests/` passam.

## 9. Defeitos e pendências conhecidos

| Gravidade | Descrição | Correção recomendada |
|---|---|---|
| Crítica | `ipSecurityRestrictionsDefaultAction: 'Deny'` sem nenhuma regra de permissão: a API fica inacessível para qualquer origem | Acrescentar `ipSecurityRestrictions` com as faixas da rede corporativa (e do gateway, se aplicável), ou expor a API só por Private Endpoint |
| Crítica | A MI não tem `Storage Blob Data Owner` na Storage Account da Function. Os App Settings de `AzureWebJobsStorage` por identidade já estão no Bicep, mas sem o papel o host não acessa a conta e não inicia | Conceder o papel à MI na conta. Definir se a concessão fica neste Bicep ou no Terraform de plataforma, com o restante dos papéis |
| Crítica | Falta a Access Policy da MI da API no Key Vault; sem ela a referência não resolve e os tokens não chegam ao app. O `keyVaultReferenceIdentity` já aponta para a MI e `auth.py` já recusa o texto literal da referência, então o sintoma é 401 em tudo, não um token previsível | Criar a Access Policy da MI com permissão `Get` de segredo, no Terraform de plataforma |
| Baixa | Sem Application Insights configurado | Acrescentar `APPLICATIONINSIGHTS_CONNECTION_STRING` |
