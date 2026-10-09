# 11 — Job `sync-expectations`

Mantém `tbl_cfg_execution_expectations` preenchida sem cadastro manual, para o [coletor](10-observability-collector.md) detectar ausência de execução e job travado. Lê duas fontes automáticas — agendamentos nativos do Databricks e `ScheduleTrigger` do ADF — e grava cada expectativa já normalizada, sem expressão de cron para o avaliador interpretar.

Jobs disparados por agendador externo (ex.: Control-M), que não têm `schedule` nativo em nenhuma das duas APIs, são cadastrados por fora deste job: `config_source = 'MANUAL'`, via CRUD do observability-api. Até esta versão, esse caso era cadastrado em `executions/manual-expectations.yaml`, lido em tempo de execução do repositório Azure Repos `OBSERV-CONFIG` (Items API). Essa dependência foi eliminada — ver [Pendências conhecidas](#pendências-conhecidas) e as atividades de acompanhamento no Azure Boards (10827, 10828, 10829).

| Item | Valor |
|---|---|
| Tipo | Azure Container Apps Job, agendado |
| Código | [src/sync-expectations/](../src/sync-expectations/) (`sync_execution_expectations.py`, `constants.py`) |
| Cadência | Diária, 05:00 UTC (`0 5 * * *`; o cron do Container Apps é UTC) |
| Identidade | `mi-observ-coletor-{dev-,}vcbr` |
| Build | Contexto de build é a própria pasta do projeto: nada é copiado de fora para a imagem |

## Entradas e saída

| De onde | O quê | Autenticação |
|---|---|---|
| Azure SQL | `tbl_cfg_workspaces` (ativos) | MI |
| Databricks, cada workspace | `GET /api/2.2/jobs/list` (100 por página): `schedule.quartz_cron_expression`, `timezone_id`, `pause_status`, `timeout_seconds`, `name` | MI |
| Azure Resource Manager | `POST .../factories/{factory}/triggers/querytriggers` (`2018-06-01`), para cada factory de `ADF_FACTORIES` | MI (`management.azure.com`) |

Saída: `tbl_cfg_execution_expectations` — `MERGE` por `(source, natural_key)` e desativação (`active = 0`) do que sumiu da fonte. Nada é apagado.

`config_source = 'MANUAL'` (job com agendamento externo, ex.: Control-M) não é entrada nem saída deste job: é escrito direto na tabela pelo observability-api (CRUD), e a sincronização AUTO nunca lê, atualiza nem desativa essas linhas (ver Convivência).

## Fluxo

1. Lê os workspaces ativos.
2. **Databricks.** Ignora job sem `schedule`, `PAUSED` ou com cron que não normaliza (com aviso). Gera `source = 'DATABRICKS'`, `natural_key = '<workspace_id>:<job_id>'` (minúsculo), fuso `timezone_id` (padrão `America/Sao_Paulo`), `tolerance_minutes = 15`, `max_expected_duration_min = timeout_seconds / 60`, `observes_national_holidays = 1`, `config_source = 'AUTO'`. Workspace com erro é logado e pulado.
3. **ADF**, só se `ADF_FACTORIES` não for vazia: considera `ScheduleTrigger` com `runtimeState = 'Started'` e recorrência normalizável; uma expectativa por pipeline do trigger, `natural_key = '<factory>/<pipeline>'`, `config_source = 'AUTO'`.
4. **Persistência**, por bloco (origem, `config_source = 'AUTO'`): lista vazia **não altera nada** (proteção contra timeout de API apagando tudo). Senão, `MERGE` de cada item (atualiza só se a linha existente tiver o mesmo `config_source`, senão insere) e desativa as linhas ativas do mesmo bloco cujo `natural_key` não veio.

## Regras de normalização

- **Cron Quartz (Databricks).** Aceita 6 ou 7 campos, segundo `0`, mês `*`. Reconhece: minutos (`0 */N * * * ?` → `INTERVAL`/`MINUTE`), horas com minuto fixo (`0 M */N * * ?` → `INTERVAL`/`HOUR`), mensal com dia fixo ou `L` (`MONTHLY`) e diário/semanal com horário fixo (`TIME_OF_DAY` com `days_of_week`). Qualquer outra forma (vários horários, faixas como `MON-FRI`, dias numéricos, `#`, `0/N`) não gera expectativa — melhor nenhuma do que uma errada.
- **Recorrência ADF.** `Hour` sem horas fixas → `INTERVAL`/`HOUR`. `Day` com uma hora e `interval = 1` → `TIME_OF_DAY`. `Week` com uma hora, dias da semana e `interval = 1` → `TIME_OF_DAY` com `days_of_week`. O resto não gera expectativa.
- **Manual (`config_source = 'MANUAL'`).** Cadastrado via CRUD do observability-api, fora deste job — a `natural_key` precisa ser **idêntica** à que o coletor gera (`<workspace_id>:<job_id>` ou `<factory>/<pipeline>`); se não for, a expectativa nunca casa com execução alguma, sem erro visível.
- **Convivência.** Cada `config_source` só atualiza e desativa as linhas que criou. Linhas `MANUAL` (criadas pela API) nunca são tocadas por este job.

## Configuração

| Variável | Uso |
|---|---|
| `AZURE_CLIENT_ID`, `SQL_SERVER_FQDN`, `SQL_DATABASE` | Identidade e hub (Bicep) |
| `ADF_FACTORIES`, `AZURE_SUBSCRIPTION_ID`, `ADF_RESOURCE_GROUP` | Lista CSV de factories e onde estão (um único resource group). **Não definidas pelo Bicep** (ver pendências) |

## Permissões

| Onde | O quê |
|---|---|
| Databricks | MI registrada e com `CAN_VIEW` nos jobs agendados (ou admin) |
| Azure SQL | `db_datareader`, `db_datawriter` |
| Data Factories | Ação `Microsoft.DataFactory/factories/querytriggers/action` (ex.: `Data Factory Contributor`); `Reader` não basta para `POST` |

## Critérios de aceite

Cada job Databricks agendado e não pausado tem linha ativa `AUTO`; pausar e rodar de novo a desativa. Cada pipeline com `ScheduleTrigger` iniciado tem linha `ADF`. Cada expectativa manual cadastrada via observability-api tem linha `MANUAL`, nunca tocada por este job. Falha de leitura de uma fonte não altera as linhas dela.

## Pendências conhecidas

| Gravidade | Descrição | Direção |
|---|---|---|
| Crítica | Bicep não define `ADF_FACTORIES`, `AZURE_SUBSCRIPTION_ID`, `ADF_RESOURCE_GROUP`: o ramo ADF é pulado em silêncio e a ausência de pipeline ADF nunca é detectada | Acrescentar ao Bicep/`params.json`; melhor, descobrir factories por Azure Resource Graph |
| Crítica | Estágios `deployment` do template não fazem checkout (ver [10](10-observability-collector.md)) | `- checkout: self` |
| Alta | Desativação sobre todas as linhas `DATABRICKS`/`AUTO`: se um workspace falhar na leitura, as expectativas dele são **desativadas** até o dia seguinte | Desativar por workspace (prefixo `<workspace_id>:`) e só os lidos com sucesso |
| Média | `querytriggers` é `POST` e exige papel específico | Conceder o papel ou trocar por `GET .../triggers` |
| Baixa | Cron com faixa de dias (`MON-FRI`) ou dia numérico não é normalizado | Expandir `_quartz_to_schedule` |
| Baixa | `0 5 * * *` em UTC é 02:00 em São Paulo | Confirmar o horário pretendido |

## Histórico

Até esta versão, o job disparado por agendador externo (Control-M) era cadastrado em `executions/manual-expectations.yaml`, versionado no repositório Azure Repos `OBSERV-CONFIG` e lido em tempo de execução via Items API (Managed Identity, branch `main`). Essa dependência foi eliminada: o caminho `MANUAL` (CRUD do observability-api, direto em `tbl_cfg_execution_expectations`) cobre o mesmo caso sem repositório de configuração externo. Acompanhamento no Azure Boards: eliminação do `OBSERV-CONFIG` (10827), documentação do novo rumo (10828) e CRUD no observability-api (10829).
