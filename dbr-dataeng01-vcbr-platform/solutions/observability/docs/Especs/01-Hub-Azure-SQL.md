# 01 - Azure SQL Database - Hub operacional

| Item | Valor |
|---|---|
| Tipo de componente | Banco de dados transacional (estado operacional da solução) |
| Repositório | `dbr-dataeng01-vcbr-platform` |
| Arquivo desta spec | `solutions/observability/docs/Especs/01-Hub-Azure-SQL.md` |
| Código | `solutions/observability/src/azure-sql/01-schema-azure-sql.sql` e `02-views.sql` |
| Recurso Azure | Servidor `sqlsrv-observ-{dev-,}vcbr`, banco `db-observ-{dev-,}vcbr` (Standard S0/S1, Private Endpoint, autenticação somente Entra ID) |
| Provisionado por | `terraform/platform/` (servidor, banco, Private Endpoint, DNS privado). O DDL é aplicado à mão, uma vez por ambiente |
| Quem escreve | observability-collector, sync-expectations, hub-publisher, hub-replication (só `tbl_ctl_replication`), observability-api (só `tbl_cfg_*`) |
| Quem lê | Os mesmos, mais o Power BI (DirectQuery sobre as views, pelo Data Gateway) |
| Especificações relacionadas | [02](02-Resolucao-de-Eventos.md), [10](10-Job-observability-collector.md), [11](11-Job-sync-expectations.md), [12](12-Job-hub-publisher.md), [13](13-Job-hub-replication.md), [30](30-Azure-Functions-observability-api.md) |

## 1. Objetivo funcional

O hub é o único lugar onde mora o estado de um incidente. Cada incidente é uma linha de `tbl_problems` com `incident_status` igual a `OPEN` ou `RESOLVED`; abrir, incrementar e fechar são operações sobre essa linha, decididas exclusivamente pela função `resolve_event()` (especificação 02). Tudo o mais no banco existe para alimentar essa decisão (configuração, watermarks, execuções observadas) ou para consumi-la (fila de notificação, views do Power BI).

O banco guarda 30 dias de dado operacional. O histórico longo vai para Delta pela replicação (especificação 13) e só então pode ser purgado.

## 2. Modelo de dados

Todas as datas são `DATETIMEOFFSET(3)` gravadas em UTC. A collation do banco deve ser a padrão *case-insensitive* (`SQL_Latin1_General_CP1_CI_AS`); a aplicação normaliza as chaves para minúsculas, e os dois níveis precisam concordar. Não existe stored procedure: toda regra de negócio está em Python.

### 2.1 Referência e configuração

| Tabela | Chave | Conteúdo | Quem mantém |
|---|---|---|---|
| `tbl_ref_source` | `source` | Origens válidas de evento: `ADF`, `DATABRICKS`, `DATA_QUALITY`, `FRESHNESS`, `VOLUME`, `SCHEMA`, `LINEAGE`. Semeada pelo script | DDL. Uma origem nova é um `INSERT` |
| `tbl_cfg_workspaces` | `workspace_id` | Workspaces Databricks que o coletor e o sync-expectations varrem (`workspace_url` completo, com `https://`) | API (`/config/workspaces`) ou `INSERT` manual |
| `tbl_cfg_domain_scope` | `source`, `scope` | Traduz escopo técnico (nome do Data Factory, `workspace_id`) em domínio de negócio | API (`/config/domains`) |
| `tbl_cfg_alert_destinations` | `destination` | Quem recebe os alertas: nome do destino (`coe`, `suporte-logistica`, ...), `channel` (`teams`), `secret_ref` (`kv://<segredo do webhook>`, nunca a URL), `description`, `active`, `updated_by`, `updated_at`. Semeada com o destino padrão do ambiente | API (`/config/alert-destinations`) |
| `tbl_cfg_alert_rules` | `object_pattern`, `failure_type`, `severity` | Regras de roteamento: para cada objeto (exato ou parcial), tipo de falha e severidade, a lista de destinos (`destinations`, separados por vírgula), mais `description`, `active`, `updated_by`, `updated_at`. Vence a regra mais específica que casar (seção 2.6). Semeada com a regra `* / * / *` para o destino padrão | API (`/config/alert-rules`) |
| `tbl_cfg_execution_expectations` | `source`, `natural_key` | Expectativa de execução já normalizada (`schedule_kind`, horário, dias, feriado, tolerância, duração máxima) | sync-expectations (`AUTO`) e API (`MANUAL`, inclusive jobs disparados pelo Control-M) |

### 2.2 Controle

| Tabela | Chave | Conteúdo | Quem mantém |
|---|---|---|---|
| `tbl_ctl_watermark` | `source` | Marca de leitura por fonte: `DBX:<workspace_id>` (varredura de concluídos), `ADF:PipelineRun` (sinal ADF), `DBX_LA:reconciliation` (rede de apoio) | Coletor |
| `tbl_ctl_replication` | `target` | Uma linha `delta` com `replicated_watermark`, semeada com `1900-01-01` | hub-replication |
| `tbl_ctl_reconciliation_gaps` | `workspace_id`, `run_id` | Candidatos a lacuna da rede de apoio, guardados entre ciclos | Coletor |

### 2.3 Execuções e incidentes

| Tabela | Chave | Conteúdo |
|---|---|---|
| `tbl_active_executions` | `workspace_id`, `run_id` | Foto do que a Jobs API diz estar rodando agora. Reconciliada a cada ciclo (linhas que somem da API são apagadas) |
| `tbl_executions` | `source`, `scope`, `run_id`, `observed_at` | Log *append-only* de toda execução observada (ADF e Databricks), com `capture_source` = `API` ou `LOG_ANALYTICS` |
| `tbl_problems` | `problem_id` (GUID sequencial) | O incidente. `correlation_key` identifica o incidente; `natural_key` identifica a origem do sinal |
| `tbl_technical_events` | `event_id` | N eventos por incidente (FK com `ON DELETE CASCADE`). Guarda `source_event_id` para idempotência |
| `tbl_notification_outbox` | `outbox_id` (identity) | Intenção de notificar (`type` = `OPEN` ou `CLOSE`), gravada na mesma transação que muda o incidente. `sent_at` nulo significa pendente; fica preenchido quando todos os destinos receberam |
| `tbl_notification_deliveries` | `outbox_id`, `destination` | Uma entrega por destino da regra escolhida: `sent_at`, `attempt_count`, `last_error`. Permite reenviar só para o destino que falhou |

### 2.4 Índices que são regra de negócio

O índice único filtrado `ux_problems_open (correlation_key) WHERE incident_status = 'OPEN'` é a garantia de que nunca existem dois incidentes abertos para a mesma correlação. Ele não pode ser removido nem trocado por um índice sobre `(source, natural_key)`. O índice único filtrado `ux_events_idempotency (source, source_event_id)` impede que um evento reenviado gere efeito duas vezes. Os demais índices (`ix_problems_open`, `ix_problems_closed`, `ix_outbox_pending`, `ix_executions_key`, `ix_events_problem`) são de desempenho.

### 2.5 Views

| View | Uso | Regras |
|---|---|---|
| `vw_pending_notifications` | Fila lida pela etapa de notificação do coletor | Junta outbox e incidente, com o objeto, o tipo de falha e a severidade de cada notificação pendente. Não escolhe destino: a escolha é do coletor, pelas regras de `tbl_cfg_alert_rules` |
| `vw_open_problems` | Contrato de DirectQuery do Power BI e de `GET /api/problems` | Só `OPEN`; calcula `open_minutes`; trunca `error_description` em 300 caracteres |
| `vw_recently_closed_problems` | Contrato de DirectQuery e de `GET /api/problems/closed` | Só `RESOLVED` fechados hoje e ontem (UTC); calcula `duration_minutes`; mesma truncagem |

O Power BI e a API leem somente as views, nunca as tabelas.

### 2.6 Regras de alerta

O destino de cada notificação sai da regra de `tbl_cfg_alert_rules` mais específica que casar com o incidente. DEV e PRD têm bancos próprios, então cada ambiente tem as suas regras.

| Campo | Valores |
|---|---|
| `failure_type` | `asset` (origens `FRESHNESS` e `VOLUME`), `data_quality`, `schema`, `pipeline` (ADF e Databricks: falha, ausência de execução, job travado) ou `*` |
| `severity` | `HIGH`, `MEDIUM`, `LOW` ou `*` |
| `object_pattern` | `*`; para dados, `<catálogo>`, `<catálogo>.<schema>` ou `<catálogo>.<schema>.<tabela>`; para pipelines, `adf:<factory>`, `adf:<factory>/<pipeline>`, `adf:<factory>/<pipeline>/<atividade>`, `databricks:workspace/<workspace_id>`, `databricks:job/<nome do job>` ou `databricks:notebook/<caminho>` |

O objeto casa por partes inteiras do nome, sem diferenciar maiúsculas. A especificidade do objeto, do menos para o mais específico, é `*` < catálogo < schema < tabela; para pipelines, `*` < `databricks:workspace` < factory < pipeline < atividade < `databricks:job` ou `databricks:notebook`. Assim, uma regra para um job Databricks vence a regra do Data Factory que o disparou. Com o mesmo objeto, `failure_type` exato vence `*`, e depois `severity` exata vence `*`. A regra vencedora pode ter vários destinos, e todos recebem.

O objeto do incidente vem do `natural_key`: nome da tabela para `FRESHNESS`, `VOLUME` e `SCHEMA`; a tabela depois do `@` para `DATA_QUALITY`; para ADF e Databricks, o Data Factory, o pipeline e a atividade, e o nome do job ou o caminho do notebook.

Exemplo:

| `object_pattern` | `failure_type` | `destinations` |
|---|---|---|
| `*` | `asset` | `coe` |
| `prd_governance` | `asset` | `suporte-logistica` |
| `prd_governance.corp_cleansed` | `asset` | `coe` |
| `prd_governance.corp_cleansed.tbl_dbr_it_asset_observability` | `asset` | `dhauz` |
| `adf:DF-CORPORATIVO-VCBR` | `pipeline` | `coe` |
| `databricks:job/vc-observability-asset-observability` | `pipeline` | `suporte-logistica, coe` |

Uma falha de asset em `prd_governance.corp_cleansed.outra_tabela` vai para o CoE; em `prd_engemix.x.y`, cai no `*` e vai para o CoE. A falha do job `vc-observability-asset-observability`, mesmo disparado pelo `DF-CORPORATIVO-VCBR`, vai para a Logística e o CoE. Toda instalação precisa da regra `* / * / *`: sem regra que case, a notificação fica pendente e o coletor registra o erro.

## 3. Regras gerais

A purga é assimétrica. Incidente `OPEN` nunca é apagado por idade. Execuções e incidentes `RESOLVED` só podem ser apagados quando as duas condições forem verdadeiras: ter mais de 30 dias e estar abaixo de `tbl_ctl_replication.replicated_watermark`. As tabelas filhas saem por cascata. O SQL de referência está no §10 do documento principal.

Nenhum componente passa `incident_status` para o banco; quem decide é `resolve_event()`. A API só expõe CRUD de tabelas `tbl_cfg_*`.

## 4. Segurança e permissões

O servidor aceita apenas autenticação Entra ID. Os usuários de banco são criados a partir das identidades gerenciadas, pelo administrador Entra do servidor, com o bloco comentado no fim do `01-schema-azure-sql.sql`. A matriz efetivamente necessária, considerando o código atual, é esta:

| Identidade | Permissão necessária | Motivo |
|---|---|---|
| `mi-observ-coletor-{dev-,}vcbr` (coletor, sync-expectations, hub-publisher, hub-replication) | `db_datareader` e `db_datawriter` | Os quatro jobs leem e gravam em várias tabelas, inclusive `tbl_problems`, `tbl_cfg_execution_expectations` e `tbl_ctl_*` |
| `mi-observ-api-{dev-,}vcbr` (API) | `SELECT` em `vw_open_problems` e `vw_recently_closed_problems`; `SELECT, INSERT, UPDATE, DELETE` em `tbl_cfg_workspaces`, `tbl_cfg_alert_destinations`, `tbl_cfg_alert_rules`, `tbl_cfg_execution_expectations`, `tbl_cfg_domain_scope`; `SELECT` em `tbl_ref_source` | A API só lê incidentes pelas views e faz CRUD de configuração |
| Grupos `Data Lake - Administrador de dados` e `Data Lake - Sustentacao` | `db_datareader`, `db_datawriter`, `db_ddladmin` | Pedido do time de plataforma |

## 5. Implantação

1. Garantir que o servidor e o banco existem (`terraform/platform/`) e que quem executa é administrador Entra do servidor.
2. Executar, nesta ordem, conectado pela rede privada (VNet ou VPN que resolva `privatelink.database.windows.net`):
   `sqlcmd -S sqlsrv-observ-dev-vcbr.database.windows.net -d db-observ-dev-vcbr -G -i src/azure-sql/01-schema-azure-sql.sql` e depois o mesmo com `02-views.sql`.
3. Executar o bloco de usuários (seção 4), trocando os nomes pelos do ambiente.
4. Cadastrar ao menos um workspace em `tbl_cfg_workspaces`, conferir o destino padrão em `tbl_cfg_alert_destinations` (com o nome real do segredo do webhook no Key Vault) e a regra `* / * / *` em `tbl_cfg_alert_rules`. Em DEV, o destino padrão é o canal de testes.
5. Semear os watermarks antes da primeira execução do coletor: `INSERT INTO tbl_ctl_watermark (source, watermark)` com o instante atual para `ADF:PipelineRun`, `DBX_LA:reconciliation` e um `DBX:<workspace_id>` por workspace. Sem isso, o primeiro ciclo varre todo o histórico disponível e abre incidentes para falhas antigas.

## 6. Critérios de aceite

O script roda do início ao fim sem erro em banco vazio. `SELECT * FROM vw_open_problems`, `vw_recently_closed_problems` e `vw_pending_notifications` executam. Dois `INSERT` com a mesma `correlation_key` e `incident_status = 'OPEN'` falham com erro 2601. Um `DELETE` em `tbl_problems` remove os eventos e a outbox filhos. Cada identidade consegue exatamente o que a seção 4 prevê e nada além.
