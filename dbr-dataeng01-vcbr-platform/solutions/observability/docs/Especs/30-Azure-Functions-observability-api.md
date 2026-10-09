# 30 - Azure Functions - observability-api

| Item | Valor |
|---|---|
| Tipo de componente | Azure Functions, plano Flex Consumption, Python 3.11, modelo de programação v2 (decorators) |
| Repositório | `AF-OBSERV-VCBR` (criado, ainda vazio) |
| Arquivo desta spec | `solutions/observability/docs/Especs/30-Azure-Functions-observability-api.md` |
| Código | `src/azure-functions/`: `function_app.py` (rotas), `auth.py`, `db.py`, `repository.py`, `resources.py`, `constants.py`, `host.json` |
| Infra | `infra/main.bicep` + `infra/params.json`; esteira própria `azure-pipelines.yml` (zip, sem imagem) |
| Recurso | `AF-OBSERV-{DEV-,}VCBR`, plano `<app>-plan`, Storage `stobserv{dev,}vcbr` |
| Identidade | `mi-observ-api-{dev-,}vcbr` |
| Especificações relacionadas | [01](01-Hub-Azure-SQL.md) |

## 1. Objetivo funcional

Ser a porta de acesso ao hub para pessoas e sistemas: consultar incidentes abertos e recém-fechados e manter as tabelas de configuração (workspaces, destinos e regras de alerta, expectativas e mapeamento de domínio) sem que ninguém precise de conexão direta com o banco. É o único caminho de manutenção da configuração usada pelos Container Apps Jobs, que não leem YAML ([especificação 40](40-Configuracao-YAML.md)). Não grava incidentes nem execuções; isso é exclusivo de `resolve_event()`.

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
| `GET` | `/api/config/alert-rules/resolve` | leitura | Regra vencedora para um objeto (mesma lógica do coletor, especificação 01, seção 2.6) e seus destinos | `object`, `failure_type`, `severity` |

| Recurso | Tabela | Chave | Obrigatórios na criação | Somente leitura |
|---|---|---|---|---|
| `workspaces` | `tbl_cfg_workspaces` | `workspace_id` | `workspace_id`, `workspace_name`, `workspace_url` | `synced_at` |
| `alert-destinations` | `tbl_cfg_alert_destinations` | `destination` | `destination`, `channel`, `secret_ref`, `updated_by` | `updated_at` |
| `alert-rules` | `tbl_cfg_alert_rules` | `object_pattern~failure_type~severity` | os três, mais `destinations` e `updated_by` | `updated_at` |
| `expectations` | `tbl_cfg_execution_expectations` | `source~natural_key` | `source`, `natural_key`, `schedule_kind`, `written_by` | `updated_at` |
| `domains` | `tbl_cfg_domain_scope` | `source~scope` | `source`, `scope`, `domain` | — |

Códigos de retorno: `200` leitura/atualização, `201` criação, `204` remoção, `400` corpo inválido, campo desconhecido, campo somente leitura, chave com número errado de partes ou `limit` não numérico, `401` token ausente, malformado ou inválido (ou token não configurado), `404` recurso ou linha inexistente, `409` chave já existe, `500` erro de banco.

## 3. Regras de negócio

Dois tokens, dois escopos: `TOKEN_READ` para `GET` e `TOKEN_WRITE` para `POST`/`PATCH`/`PUT`/`DELETE`; o token de escrita não dá leitura e vice-versa. Sem variável configurada, ninguém entra (falha fechada). A comparação é em tempo constante. Nomes de tabela e coluna vêm de `resources.py`; todo valor vai como parâmetro do `pyodbc`. Campo desconhecido no corpo é rejeitado, nunca ignorado. Só tabelas `tbl_cfg_*` podem ser recurso (há teste que garante). Expectativas criadas pela API devem usar `config_source = 'MANUAL'`, que o sync-expectations nunca altera; é assim que se cadastram os jobs disparados pelo Control-M.

Validação de destinos e regras de alerta: `channel` só `teams`; `secret_ref` começa por `kv://` (nunca URL); `failure_type` em `asset`, `data_quality`, `schema`, `pipeline` ou `*`; `severity` em `HIGH`, `MEDIUM`, `LOW` ou `*`; `object_pattern` num dos formatos da especificação 01, seção 2.6; todo destino citado em `destinations` existe e está ativo. Remover ou desativar um destino citado por uma regra ativa devolve `409`. Toda escrita grava `updated_by` e `updated_at`. Como o `object_pattern` pode conter `/`, a chave na URL vai codificada (`%2F`).

## 4. Integrações

| Com | Como |
|---|---|
| Azure SQL | `pyodbc` com `SQL_CONNECTION_STRING` (montada pelo Bicep com ODBC Driver 18) e token Entra da MI injetado no driver; conexão aberta e fechada por requisição |
| Key Vault | `TOKEN_READ` e `TOKEN_WRITE` como Key Vault references (`@Microsoft.KeyVault(SecretUri=...)`) |
| Rede | VNet integration na subnet `SUBNET-PRIVATE-AF-OBSERV-{DEV,PRD}-VCBR` para alcançar o Private Endpoint do banco |

## 5. Configuração (App Settings)

| Variável | Valor |
|---|---|
| `AZURE_CLIENT_ID` | Client ID da MI |
| `SQL_CONNECTION_STRING` | `Driver={ODBC Driver 18 for SQL Server};Server=tcp:<servidor>.database.windows.net,1433;Database=<banco>;Encrypt=yes;` |
| `TOKEN_READ`, `TOKEN_WRITE` | Referências ao Key Vault (parâmetros `refTokenRead`/`refTokenWrite`) |
| `ENVIRONMENT` | `dev` ou `prod` |
| `AzureWebJobsStorage__accountName` e `AzureWebJobsStorage__credential`/`__clientId` | Bicep, por identidade |

## 6. Permissões

A MI precisa das permissões de banco da especificação 01 (CRUD em `tbl_cfg_*`, leitura das duas views), de `Get` de segredo no Key Vault (Access Policy) e de `Storage Blob Data Owner` na Storage Account da Function (pacote de implantação e `AzureWebJobsStorage`). A subnet precisa estar delegada a `Microsoft.App/environments` (exigência do Flex Consumption).

## 7. Implantação

A esteira testa, empacota a pasta em zip (sem testes, infra e README), aplica o Bicep e publica com `az functionapp deploy --type zip --build-remote true`, primeiro em dev e, com aprovação, em prod. Antes do primeiro deploy: criar os dois segredos no Key Vault, preencher `vnetResourceGroup`, `refTokenRead` e `refTokenWrite` em `params.json`.

## 8. Critérios de aceite

`GET /api/problems` sem token devolve 401; com o token de escrita, 401; com o de leitura, 200 e a lista de incidentes abertos. `POST /api/config/alert-rules` com o token de escrita cria a regra e um segundo `POST` igual devolve 409; uma regra com destino inexistente devolve 400. `GET /api/config/alert-rules/resolve?object=prd_governance.corp_cleansed.x&failure_type=asset&severity=HIGH` devolve a regra mais específica e os destinos. `GET /api/config/expectations?source=ADF` filtra. Um corpo com campo inexistente devolve 400.
