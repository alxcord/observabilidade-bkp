# 14 — Job `hub-schema-init`

Cria o schema do hub ([01](01-hub-azure-sql.md)) por Python, sem cliente SQL. O DDL está **só** dentro do código do job ([schema_definition.py](../src/hub-schema-init/schema_definition.py)), sem cópia em arquivo `.sql` e ele roda dentro do Container Apps Environment, que já tem rota até o Private Endpoint do banco. Também cria os usuários de banco das identidades e concede permissões, quando pedido.

| Item | Valor |
|---|---|
| Tipo | Azure Container Apps Job, **manual** (sem agenda) |
| Código | [src/hub-schema-init/](../src/hub-schema-init/) (`schema_init.py`, `schema_definition.py` com o DDL embutido, `constants.py`) |
| Identidade | Por padrão `mi-observ-coletor-{dev-,}vcbr`, **com ressalvas de permissão** (abaixo) |

## Entradas e saídas

Entradas: DDL embutido em `SCHEMA_BATCHES` (a imagem não precisa de `.sql`); `SQL_SERVER_FQDN`, `SQL_DATABASE`, `ENVIRONMENT`, `AZURE_CLIENT_ID` (Bicep); `SQL_ADMIN_GROUPS` opcional, separado por vírgula (padrão `Data Lake - Administrador de dados` e `Data Lake - Sustentacao`).

Saídas: só o que faltar (tabelas, índices, dados iniciais de `tbl_ref_source`, rotas `DEFAULT` e `tbl_ctl_replication`, views); usuários e permissões apenas com `--grant-users`; resumo no log; código `0` em sucesso, diferente de zero em qualquer falha ou divergência de estrutura.

| Argumento | Efeito |
|---|---|
| (nenhum) | Cria o que faltar e confere o que existe |
| `--dry-run` | Mostra o que faria, sem executar nem gravar |
| `--grant-users` | Depois do schema, cria usuários e concede permissões |

Com Container Apps Job: `az containerapp job start -n hub-schema-init-dev -g RG-ANALYTICS-DEV-VCBR --args "--dry-run"`.

## Como aplica o schema

Percorre `SCHEMA_BATCHES` na ordem (um comando T-SQL por item, sem `GO`). Comando de tipo desconhecido interrompe a execução **antes** de qualquer alteração: só sabe tornar idempotentes estes quatro tipos.

| Comando | Comportamento |
|---|---|
| `CREATE TABLE` | Não existe → cria. Existe → **confere** nome e tipo de cada coluna (`INFORMATION_SCHEMA.COLUMNS`) e não altera nada |
| `CREATE INDEX` | Cria se faltar, senão pula |
| `INSERT` | Só executa se a tabela foi criada **nesta execução** (dados iniciais nunca são reinseridos) |
| `CREATE OR ALTER VIEW` | Sempre executa (é o que atualiza a view) |

Tudo corre numa única transação: falha de comando ou divergência numa tabela existente → nada é gravado e o erro lista cada divergência (ex.: `tbl_problems.severity: tipo varchar(5), esperado varchar(20)`).

**Regras de estrutura.** A conferência compara só nome e tipo (com tamanho, e precisão para `datetimeoffset`/`time`); não compara anulabilidade, padrão, chaves nem restrições. Coluna a mais no banco passa; ausente ou de outro tipo reprova. O job **não corrige** divergência — alterar tabela com dados é decisão de uma pessoa. Para evoluir o schema, acrescente a coluna ao `CREATE TABLE` de `schema_definition.py` (tabelas em ordem de dependência) e aplique o `ALTER TABLE` correspondente no banco.

## Usuários e permissões (`--grant-users`)

Cria o usuário `FROM EXTERNAL PROVIDER` se não existir e concede (repetir é seguro). Os nomes dependem de `ENVIRONMENT` (`prod` sem sufixo; qualquer outro valor usa `-dev`).

| Identidade | Permissão |
|---|---|
| `mi-observ-coletor-{dev-,}vcbr` | `db_datareader`, `db_datawriter` |
| `mi-observ-api-{dev-,}vcbr` | `SELECT` em `tbl_ref_source`, `vw_open_problems`, `vw_recently_closed_problems`; `SELECT/INSERT/UPDATE/DELETE` em `tbl_cfg_workspaces`, `tbl_cfg_alert_routing`, `tbl_cfg_execution_expectations`, `tbl_cfg_domain_scope`, `tbl_cfg_integration_runtimes`. Nada nas tabelas de incidente |
| Cada grupo de `SQL_ADMIN_GROUPS` | `db_datareader`, `db_datawriter`, `db_ddladmin` |

## Implantação

A esteira compartilhada testa, constrói a imagem e aplica o Bicep em dev e prod, criando o job manual. A execução é decisão de uma pessoa. Ambiente novo: `--dry-run`, depois sem argumentos, depois `--grant-users` (ou tudo de uma vez com `--grant-users`). Em ambiente cujo schema foi criado à mão, rode sem argumentos: só confere a estrutura e atualiza as views.

## Permissões — o ponto que costuma travar

| Para | É preciso |
|---|---|
| Aplicar o schema | `db_ddladmin` (ou `db_owner`) **e** já existir como usuário do banco |
| `--grant-users` | Ser administradora Entra do servidor SQL (`CREATE USER ... FROM EXTERNAL PROVIDER` exige) |

A identidade padrão dos jobs (`mi-observ-coletor-*`) não tem nenhum dos dois. Dois caminhos, ambos temporários: (1) adicioná-la ao grupo Entra administrador do servidor (`sql_aad_admin_object_id` em `terraform/platform/`), executar e removê-la; (2) criar uma identidade só para isso, já administradora, e informá-la em `managedIdentityNameOverride` no Bicep. O Container Apps Environment precisa alcançar o Private Endpoint do banco.

## Critérios de aceite

Em banco vazio, a primeira execução cria as 14 tabelas, os índices, os dados iniciais e as 3 views. A segunda não cria nada, confere as 14 tabelas e reaplica só as views. Tabela apagada → só ela (e seus índices e dados iniciais) é recriada. Coluna ausente ou de tipo diferente reprova sem gravar nada. Falha forçada no meio deixa o banco como estava. Com `--grant-users`, as três identidades e os dois grupos têm exatamente as permissões acima.

## Validação feita e limites

Classificador testado contra o DDL embutido (27 comandos reconhecidos); o DDL foi conferido, tabela a tabela (colunas, tipos e restrições), contra a versão original com `.sql`; leitura de colunas e tipos conferida nas 13 tabelas originais (a 14ª, `tbl_cfg_integration_runtimes`, entrou depois e só tem teste com banco simulado); decisão criar/conferir/pular, atomicidade e SQL de permissões testados com banco simulado. **Não foi executado contra SQL Server real**: sintaxe T-SQL, consulta a `INFORMATION_SCHEMA` e permissões só se confirmam na primeira execução em dev, que deve começar por `--dry-run`.

## Pendências conhecidas

| Gravidade | Descrição |
|---|---|
| Baixa | A conferência não compara anulabilidade, padrões nem restrições |
| Baixa | Dois jobs simultâneos poderiam disputar o schema; o Bicep fixa `parallelism: 1`, mas não há trava no banco |
