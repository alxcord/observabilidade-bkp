# 40 — Configuração de negócio (YAML)

**Em descontinuação.** O repositório Azure Repos `OBSERV-CONFIG` (projeto `VC-ANALYTICS`) está sendo eliminado como fonte de configuração — ver atividades no Azure Boards: eliminação do repositório (10827), documentação do novo rumo (10828, este documento) e CRUD no observability-api (10829). A direção é mover cada arquivo para uma tabela SQL mantida por API/CRUD, com auditoria básica (quem alterou, quando), em vez de arquivo versionado lido em tempo de execução.

Situação de cada arquivo:

| Arquivo | Consumido por | Situação |
|---|---|---|
| `executions/manual-expectations.yaml` | `sync-expectations` (este repositório) | **Eliminado.** `sync-expectations` não lê mais o YAML/Items API — o caso (job com agendamento externo, ex.: Control-M) agora é `config_source = 'MANUAL'` em `tbl_cfg_execution_expectations`, escrito via CRUD do observability-api. Ver [doc/11](11-sync-expectations.md#histórico) |
| `quality-rules/<domínio>.yaml`, `assets/monitored-assets.yaml` | Jobs Databricks (repositório próprio, fora deste) | Ainda ativo via Git Folder do `OBSERV-CONFIG` no workspace. Migração para SQL fica a cargo do repositório que os consome — fora do escopo deste repositório |
| `alerts/routing.yaml` | **Nenhum** (ver pendência) | Já é só documentação — rotas reais vivem em `tbl_cfg_alert_routing` ([01](01-hub-azure-sql.md)), mantidas pela API |

## `manual-expectations.yaml` (histórico)

Enquanto existiu, usava a chave `expectations:`, com os campos descritos em [11](11-sync-expectations.md) (`source`, `natural_key`, `schedule_kind` obrigatórios). A `natural_key` precisa ser idêntica à gerada pelo coletor: `<workspace_id>:<job_id>` (Databricks) ou `<factory>/<pipeline>` (ADF) — essa regra continua valendo para o cadastro `MANUAL` via observability-api.

## `routing.yaml`

Chave `routes`, cada item com `domain`, `severity` (`HIGH`/`MEDIUM`/`LOW`), `channel` (`teams`; `email` não é enviado) e `destination` (`kv://<nome-do-segredo>`). A rota `DEFAULT` para as três severidades é obrigatória. Hoje o arquivo é só documentação: as rotas reais estão em `tbl_cfg_alert_routing` ([01](01-hub-azure-sql.md)), semeadas com `DEFAULT` e mantidas pela API.

## Regras de convivência

Chaves em inglês; valores de negócio como estão. Destino de alerta é sempre nome de segredo, nunca URL. Os valores de domínio precisam ser idênticos onde aparecem (tag `vc_data_domain`, regras, `routing.yaml`/`tbl_cfg_alert_routing` e `tbl_cfg_domain_scope`).

## Pendências conhecidas

| Gravidade | Descrição | Direção |
|---|---|---|
| Alta | `routing.yaml` diz ser sincronizado para `tbl_cfg_alert_routing` "por job de configuração", mas esse job não existe | Cadastrar pela API e tratar o YAML como documentação (mesmo caminho adotado para `manual-expectations.yaml`) |
| Média | `quality-rules/*.yaml` e `assets/monitored-assets.yaml` ainda dependem do `OBSERV-CONFIG` via Git Folder, fora deste repositório | Plano de migração para SQL a cargo do repositório que os consome (ver atividade 10827) |
