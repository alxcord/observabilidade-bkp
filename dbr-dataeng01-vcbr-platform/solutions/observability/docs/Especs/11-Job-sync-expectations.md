# 11 - Azure Container Apps Job - sync-expectations

| Item | Valor |
|---|---|
| Tipo de componente | Azure Container Apps Job, agendado |
| Repositório | `cae-observ-vcbr` |
| Arquivo desta spec | `solutions/observability/docs/Especs/11-Job-sync-expectations.md` |
| Código | `src/sync-expectations/sync_execution_expectations.py`, `constants.py` |
| Infra | `infra/main.bicep` + `infra/params.json`; esteira `azure-pipelines.yml` (template compartilhado) |
| Cadência | Diária, 05:00 UTC (`0 5 * * *`) — o cron do Container Apps é em UTC |
| Identidade | `mi-observ-coletor-{dev-,}vcbr` |
| Especificações relacionadas | [01](01-Hub-Azure-SQL.md), [10](10-Job-observability-collector.md), [30](30-Azure-Functions-observability-api.md) |

## 1. Objetivo funcional

Manter `tbl_cfg_execution_expectations` preenchida sem cadastro manual, para que o coletor consiga detectar ausência de execução e job travado. Lê duas fontes, os agendamentos nativos do Databricks e os `ScheduleTrigger` do ADF, e grava cada expectativa já normalizada, sem expressão de cron para o avaliador interpretar. Jobs disparados por agendador externo (Control-M), que não têm agenda em nenhuma das duas APIs, são cadastrados à mão pela `observability-api` (seção 5.3); o job não lê YAML.

## 2. Entradas

| De onde | O quê | Mecanismo | Autenticação |
|---|---|---|---|
| Azure SQL | `tbl_cfg_workspaces` (ativos) | `pyodbc` | MI |
| Databricks, cada workspace | `GET /api/2.2/jobs/list` (paginado, 100 por página): `settings.schedule.quartz_cron_expression`, `timezone_id`, `pause_status`, `settings.timeout_seconds`, `settings.name` | HTTPS | MI, recurso Databricks |
| Azure Resource Manager | `POST .../factories/{factory}/triggers/querytriggers?api-version=2018-06-01`, para cada factory de `ADF_FACTORIES` | HTTPS | MI (`https://management.azure.com/.default`) |

## 3. Saída

`tbl_cfg_execution_expectations`: `MERGE` por `(source, natural_key)` e desativação (`active = 0`) do que sumiu da fonte. Nada é apagado.

## 4. Fluxo de processamento

1. Conecta ao hub e lê os workspaces ativos.
2. **Databricks.** Para cada workspace, pagina `jobs/list`. Ignora job sem `schedule`, com `pause_status = 'PAUSED'` ou com cron que não normaliza (seção 5.1, com aviso em log). Gera uma expectativa com `source = 'DATABRICKS'`, `natural_key = '<workspace_id>:<job_id>'` (minúsculo), `description` = nome do job, fuso = `timezone_id` (padrão `America/Sao_Paulo`), `tolerance_minutes = 15`, `max_expected_duration_min = timeout_seconds / 60` (nulo sem timeout), `observes_national_holidays = 1`, `written_by = 'sync_execution_expectations:databricks'`, `source_expression` = cron original. Um workspace com erro é registrado em log e pulado. Persiste todas as expectativas Databricks juntas com `config_source = 'AUTO'`.
3. **ADF**, só se `ADF_FACTORIES` não estiver vazia. Para cada factory, consulta os triggers; considera só `ScheduleTrigger` com `runtimeState = 'Started'` e recorrência normalizável (seção 5.2). Gera uma expectativa por pipeline referenciado no trigger: `source = 'ADF'`, `natural_key = '<factory>/<pipeline>'`, `description` = nome do trigger, `max_expected_duration_min` nulo, `written_by = 'sync_execution_expectations:adf'`. Persiste com `config_source = 'AUTO'`.
4. **Persistência** (`persist`), por bloco (origem + `config_source`): se a lista vier vazia, não altera nada (proteção contra timeout de API apagando tudo). Senão, faz `MERGE` de cada expectativa — atualiza só se a linha existente tiver o mesmo `config_source`, senão insere — e depois desativa as linhas ativas da mesma origem e mesmo `config_source` cujo `natural_key` não veio nesta execução.

## 5. Regras de negócio

### 5.1 Normalização do cron Quartz (Databricks)

Aceita 6 ou 7 campos, segundo igual a `0` e mês `*`. Reconhece intervalo em minutos (`0 */N * * * ?` → `INTERVAL`/`MINUTE`/N), intervalo em horas com minuto fixo (`0 M */N * * ?` → `INTERVAL`/`HOUR`/N), mensal com dia fixo ou `L` (`0 M H D * ?` → `MONTHLY`), e diário/semanal com horário fixo (`0 M H ? * MON,WED` → `TIME_OF_DAY` com `days_of_week`). Qualquer outra forma (vários horários, faixas como `MON-FRI`, dias numéricos, `#`, `0/N`) não gera expectativa — melhor nenhuma do que uma errada.

### 5.2 Normalização da recorrência do ADF

`Hour` sem horas fixas → `INTERVAL`/`HOUR`/`interval`. `Day` com uma hora e `interval = 1` → `TIME_OF_DAY`. `Week` com uma hora, dias da semana e `interval = 1` → `TIME_OF_DAY` com `days_of_week`. Mais de uma hora ou minuto, `Month`, `Minute` ou intervalo diferente de 1 em `Day`/`Week` não geram expectativa.

### 5.3 Expectativas manuais (Control-M e outros agendadores externos)

São cadastradas em `tbl_cfg_execution_expectations` pela `observability-api` (`POST /api/config/expectations`, [especificação 30](30-Azure-Functions-observability-api.md)), que grava `config_source = 'MANUAL'`. Cada expectativa exige `source`, `natural_key` e `schedule_kind` (`INTERVAL`, `TIME_OF_DAY` ou `MONTHLY`). Campos opcionais: `description`, `interval_unit`, `interval_value`, `time_of_day` (`HH:MM`), `days_of_week` (CSV `MON..SUN`), `day_of_month`, `last_day_of_month`, `observes_national_holidays` (padrão `true`), `timezone` (padrão `America/Sao_Paulo`), `tolerance_minutes` (padrão 15), `max_expected_duration_min`. A `natural_key` precisa ser idêntica à que o coletor gera (`<workspace_id>:<job_id>` ou `<factory>/<pipeline>`); se não for, a expectativa nunca casa com execução nenhuma e o job continua sem cobertura, sem erro visível.

### 5.4 Convivência das fontes

Cada `config_source` só atualiza e desativa as linhas que ele criou. Linhas `MANUAL` (criadas pela API) nunca são tocadas. Se a mesma chave existir com `config_source` diferente, a linha existente prevalece e a nova fonte é ignorada para aquela chave.

## 6. Configuração

| Variável | Origem | Uso |
|---|---|---|
| `AZURE_CLIENT_ID`, `SQL_SERVER_FQDN`, `SQL_DATABASE` | Bicep | Identidade e hub |
| `ADF_FACTORIES` | Bicep | Lista CSV de nomes de Data Factory |
| `AZURE_SUBSCRIPTION_ID`, `ADF_RESOURCE_GROUP` | Bicep | Localização dos factories (um único resource group) |
| `LOG_ANALYTICS_WORKSPACE_ID` | Bicep | Não usada pelo código |

## 7. Pré-requisitos e permissões

| Onde | O que conceder |
|---|---|
| Databricks | MI registrada e com `CAN_VIEW` nos jobs agendados de cada workspace (ou admin) |
| Azure SQL | `db_datareader`, `db_datawriter` |
| Data Factories | Papel que permita a ação `Microsoft.DataFactory/factories/querytriggers/action` (por exemplo `Data Factory Contributor`, ou papel customizado com essa ação mais `*/read`). `Reader` não basta para um `POST` |

## 8. Critérios de aceite

Depois de uma execução, cada job Databricks agendado e não pausado tem uma linha ativa `AUTO`; pausar o job e rodar de novo desativa a linha. Cada pipeline acionado por `ScheduleTrigger` iniciado tem uma linha `ADF`. Uma expectativa `MANUAL` cadastrada pela API não é alterada nem desativada pelo job. Uma falha de leitura em uma fonte não altera as linhas dessa fonte.
