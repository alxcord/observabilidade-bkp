"""DDL do hub operacional (Azure SQL), embutido no código.

Este arquivo é a ÚNICA definição do schema: não existe cópia em arquivo .sql.
Cada item de SCHEMA_BATCHES é um comando T-SQL que o `schema_init.py`
executa, na ordem, depois de olhar o banco:

  * CREATE TABLE  -> cria se faltar; se existir, só confere nome e tipo das colunas;
  * CREATE INDEX  -> cria se faltar;
  * INSERT        -> só roda se a tabela foi criada nesta mesma execução;
  * CREATE OR ALTER VIEW -> sempre reaplicada.

Qualquer outro tipo de comando é recusado (ver `schema_init.classify`). Não há
`GO`: cada item já é um lote. Os itens ficam em ordem de dependência (uma
tabela referenciada vem antes de quem a referencia).

Convenções do schema:
  * todo timestamp é DATETIMEOFFSET(3), gravado em UTC;
  * nomes de tabela, coluna, índice e constraint em inglês; comentários em
    português;
  * collation padrão case-insensitive (SQL_Latin1_General_CP1_CI_AS): a
    aplicação normaliza as chaves de correlação para minúsculas e os dois
    níveis precisam concordar;
  * não há stored procedure: abrir, incrementar e fechar incidente é feito por
    `src/shared/event_resolution.py`.
"""

SCHEMA_BATCHES = (

    # =====================================================================
    # 1. Referência e configuração
    # =====================================================================

    r"""
-- Origens válidas de um evento. É tabela, e não CHECK constraint, para que
-- incluir uma origem nova seja um INSERT, e não uma migração de DDL.
CREATE TABLE tbl_ref_source (
  source       VARCHAR(30)   NOT NULL PRIMARY KEY,
  description  NVARCHAR(200) NULL,
  active       BIT           NOT NULL DEFAULT 1
);""",

    r"""
INSERT INTO tbl_ref_source (source, description) VALUES
  ('ADF',          N'Falha de pipeline, atividade ou trigger do Azure Data Factory; nó de Integration Runtime indisponível'),
  ('DATABRICKS',   N'Falha de job run do Databricks'),
  ('DATA_QUALITY', N'Regra de qualidade bloqueante reprovada'),
  ('FRESHNESS',    N'Ativo fora do SLA de atualidade'),
  ('VOLUME',       N'Volume fora do comportamento esperado'),
  ('SCHEMA',       N'Mudança estrutural incompatível'),
  ('LINEAGE',      N'Lacuna de linhagem em ativo crítico');""",

    r"""
-- Workspaces Databricks varridos pelo coletor e pelo sync-expectations.
-- Não há coluna de credencial: a autenticação é uma Managed Identity única.
CREATE TABLE tbl_cfg_workspaces (
  workspace_id    VARCHAR(50)       NOT NULL PRIMARY KEY,
  workspace_name  NVARCHAR(200)     NOT NULL,
  workspace_url   NVARCHAR(300)     NOT NULL,   -- URL completa, com https://
  region          VARCHAR(50)       NULL,
  environment     VARCHAR(20)       NULL,
  active          BIT               NOT NULL DEFAULT 1,
  synced_at       DATETIMEOFFSET(3) NOT NULL DEFAULT SYSDATETIMEOFFSET()
);""",

    r"""
-- Traduz o escopo técnico de um evento (nome do Data Factory, workspace_id)
-- no domínio de negócio. Sem correspondência, o incidente cai no domínio
-- 'DEFAULT' e o alerta vai para a rota de fallback: nunca desaparece.
CREATE TABLE tbl_cfg_domain_scope (
  source  VARCHAR(30)   NOT NULL REFERENCES tbl_ref_source(source),
  scope   NVARCHAR(200) NOT NULL,   -- nome do ADF, workspace_id ou catálogo
  domain  VARCHAR(50)   NOT NULL,
  active  BIT           NOT NULL DEFAULT 1,
  CONSTRAINT pk_cfg_domain_scope PRIMARY KEY (source, scope)
);""",

    r"""
-- Self-Hosted Integration Runtime monitorado pelo coletor (disponibilidade de
-- nós). Só entra aqui o ADF DONO do SHIR: Linked IR (compartilhado de outro
-- ADF) não se cadastra, porque o sinal vem do ADF dono. IR que não está nesta
-- tabela não é monitorado. Mantida pela API de configuração.
CREATE TABLE tbl_cfg_integration_runtimes (
  data_factory         NVARCHAR(200) NOT NULL,   -- nome do ADF dono do SHIR
  integration_runtime  NVARCHAR(200) NOT NULL,   -- nome do IR no ADF

  -- Onde o ADF mora: o coletor lê a métrica de nós pela API de métricas do
  -- Azure Monitor, que exige o id ARM completo do recurso. Aqui, e não em
  -- variável de ambiente, para que ADFs em assinaturas ou resource groups
  -- diferentes convivam sem mudança de código.
  subscription_id      VARCHAR(36)   NOT NULL,
  resource_group       NVARCHAR(90)  NOT NULL,

  expected_nodes       INT           NOT NULL CHECK (expected_nodes >= 1),
  active               BIT           NOT NULL DEFAULT 1,
  CONSTRAINT pk_cfg_integration_runtimes PRIMARY KEY (data_factory, integration_runtime)
);""",

    r"""
-- Para onde vai cada notificação. `destination` é uma referência ao segredo
-- no Key Vault (kv://<nome>), nunca a URL do webhook.
CREATE TABLE tbl_cfg_alert_routing (
  domain       VARCHAR(50)   NOT NULL,   -- IT, Logistics, Finance, Procurement ou DEFAULT
  severity     VARCHAR(20)   NOT NULL,   -- HIGH | MEDIUM | LOW
  channel      VARCHAR(20)   NOT NULL,   -- teams | email
  destination  NVARCHAR(500) NOT NULL,
  active       BIT           NOT NULL DEFAULT 1,
  CONSTRAINT pk_cfg_alert_routing PRIMARY KEY (domain, severity, channel)
);""",

    r"""
-- Rota de fallback obrigatória: sem ela, um incidente cujo domínio não foi
-- resolvido não teria destino e o alerta sumiria em silêncio.
INSERT INTO tbl_cfg_alert_routing (domain, severity, channel, destination) VALUES
  ('DEFAULT', 'HIGH',   'teams', N'kv://obs-webhook-ops'),
  ('DEFAULT', 'MEDIUM', 'teams', N'kv://obs-webhook-ops'),
  ('DEFAULT', 'LOW',    'teams', N'kv://obs-webhook-ops');""",

    r"""
-- Expectativa de execução já normalizada: o avaliador de ausência e de job
-- travado lê só estas colunas e nunca interpreta cron.
--
-- Quem grava:
--   * sync-expectations, a partir de jobs Databricks com schedule nativo e de
--     ScheduleTrigger do ADF (config_source = 'AUTO'), e do YAML de jobs
--     disparados por agendador externo, como o Control-M (config_source = 'YAML');
--   * a API, para cadastro manual (config_source = 'MANUAL'), que o
--     sincronizador nunca sobrescreve nem desativa.
CREATE TABLE tbl_cfg_execution_expectations (
  source       VARCHAR(30)   NOT NULL REFERENCES tbl_ref_source(source),
  natural_key  NVARCHAR(400) NOT NULL,
  description  NVARCHAR(300) NULL,

  -- Forma da expectativa. Só os campos do bloco correspondente a
  -- schedule_kind são preenchidos; os demais ficam NULL.
  schedule_kind  VARCHAR(10) NOT NULL
                   CHECK (schedule_kind IN ('INTERVAL', 'TIME_OF_DAY', 'MONTHLY')),

  -- INTERVAL: a cada N minutos ou horas, sem horário fixo.
  interval_unit   VARCHAR(10) NULL CHECK (interval_unit IN ('MINUTE', 'HOUR')),
  interval_value  INT         NULL,

  -- TIME_OF_DAY: um horário por dia, com filtro opcional de dia da semana.
  time_of_day   TIME(0)    NULL,
  days_of_week  VARCHAR(20) NULL,   -- CSV de MON..SUN; NULL = todo dia

  -- MONTHLY: um dia do mês (ou o último dia), com horário fixo.
  day_of_month       TINYINT NULL,
  last_day_of_month  BIT     NOT NULL DEFAULT 0,

  -- 1 = o job é esperado mesmo em feriado nacional; 0 = feriado não conta
  -- como ausência. Nem Databricks nem ADF informam isto: só quem declara à
  -- mão (YAML ou MANUAL) decide diferente de 1.
  observes_national_holidays  BIT NOT NULL DEFAULT 1,

  timezone                   VARCHAR(50) NOT NULL DEFAULT 'America/Sao_Paulo',
  tolerance_minutes          INT         NOT NULL DEFAULT 15,
  max_expected_duration_min  INT         NULL,

  -- Quem mantém a linha: written_by é o processo (granularidade fina, por
  -- exemplo o ramo Databricks ou ADF do sincronizador); config_source é a
  -- família de origem.
  written_by     VARCHAR(60) NOT NULL,
  config_source  VARCHAR(10) NOT NULL
                   CHECK (config_source IN ('AUTO', 'MANUAL', 'YAML')),

  -- Expressão nativa original (cron Quartz ou recurrence do ADF), guardada só
  -- para auditoria. O avaliador nunca lê esta coluna.
  source_expression  NVARCHAR(200) NULL,

  active      BIT               NOT NULL DEFAULT 1,
  updated_at  DATETIMEOFFSET(3) NOT NULL DEFAULT SYSDATETIMEOFFSET(),
  CONSTRAINT pk_cfg_execution_expectations PRIMARY KEY (source, natural_key)
);""",

    # =====================================================================
    # 2. Controle do coletor e da replicação
    # =====================================================================

    r"""
-- Marca de leitura por fonte (watermark). Chaves em uso:
--   DBX:<workspace_id>            varredura de execuções concluídas do Databricks
--   DBX_LA:reconciliation         rede de apoio (Log Analytics, DatabricksJobs)
--   ADF:PipelineRun               atividades do ADF
--   ADF:PipelineRunStatus         pipeline runs do ADF
--   ADF:TriggerRun                trigger runs do ADF
--
-- A disponibilidade de nós do Self-Hosted IR não tem watermark: ela vem da
-- API de métricas do Azure Monitor, que devolve o estado atual, e o coletor
-- olha sempre uma janela fixa para trás.
CREATE TABLE tbl_ctl_watermark (
  source      VARCHAR(60)       NOT NULL PRIMARY KEY,
  watermark   DATETIMEOFFSET(3) NOT NULL,
  updated_at  DATETIMEOFFSET(3) NOT NULL DEFAULT SYSDATETIMEOFFSET()
);""",

    r"""
-- Watermark da replicação para Delta. Há um único destino hoje; a coluna
-- `target` existe para comportar outro sem migração de schema.
CREATE TABLE tbl_ctl_replication (
  target                VARCHAR(30)       NOT NULL PRIMARY KEY,   -- 'delta'
  replicated_watermark  DATETIMEOFFSET(3) NOT NULL
);""",

    r"""
INSERT INTO tbl_ctl_replication (target, replicated_watermark)
VALUES ('delta', '1900-01-01T00:00:00.000+00:00');""",

    r"""
-- Candidatos a lacuna da rede de apoio. O coletor não guarda estado entre
-- ciclos, e um run só é tratado como lacuna depois de aparecer ausente por
-- mais de um ciclo (para não confundir com um run que a Jobs API ainda não
-- devolveu). A linha some quando o run deixa de ser candidato: a Jobs API o
-- alcançou, ou ele foi promovido a tbl_executions.
CREATE TABLE tbl_ctl_reconciliation_gaps (
  workspace_id   VARCHAR(50)       NOT NULL,
  run_id         VARCHAR(50)       NOT NULL,
  first_seen_at  DATETIMEOFFSET(3) NOT NULL DEFAULT SYSDATETIMEOFFSET(),
  CONSTRAINT pk_reconciliation_gaps PRIMARY KEY (workspace_id, run_id)
);""",

    # =====================================================================
    # 3. Execuções
    # =====================================================================

    r"""
-- Foto do que a Jobs API diz estar rodando agora. Reconciliada a cada ciclo:
-- nunca acumula.
CREATE TABLE tbl_active_executions (
  workspace_id  VARCHAR(50)       NOT NULL,
  run_id        VARCHAR(50)       NOT NULL,
  job_id        VARCHAR(50)       NULL,
  run_name      NVARCHAR(500)     NULL,
  status        VARCHAR(30)       NOT NULL,
  started_at    DATETIMEOFFSET(3) NOT NULL,
  updated_at    DATETIMEOFFSET(3) NOT NULL DEFAULT SYSDATETIMEOFFSET(),
  CONSTRAINT pk_active_executions PRIMARY KEY (workspace_id, run_id)
);""",

    r"""
-- Log append-only de toda execução observada, de ADF e de Databricks: a
-- checagem de ausência precisa dos dois lados. `scope` é NOT NULL porque
-- run_id só é único dentro de um workspace, e coluna de chave primária não
-- aceita NULL.
CREATE TABLE tbl_executions (
  source          VARCHAR(30)       NOT NULL REFERENCES tbl_ref_source(source),
  scope           VARCHAR(50)       NOT NULL,   -- workspace_id (Databricks) | nome do Data Factory (ADF)
  natural_key     NVARCHAR(400)     NOT NULL,
  run_id          VARCHAR(80)       NOT NULL,   -- run_id (Databricks) | id da execução (ADF)
  run_name        NVARCHAR(500)     NULL,
  status          VARCHAR(30)       NOT NULL,
  result_state    VARCHAR(30)       NULL,
  capture_source  VARCHAR(20)       NOT NULL DEFAULT 'API',   -- API | LOG_ANALYTICS
  observed_at     DATETIMEOFFSET(3) NOT NULL,
  CONSTRAINT pk_executions PRIMARY KEY (source, scope, run_id, observed_at)
);""",

    r"""
CREATE INDEX ix_executions_key
  ON tbl_executions (source, natural_key, observed_at DESC);""",

    # =====================================================================
    # 4. Incidentes
    # =====================================================================

    r"""
-- O incidente. `correlation_key` identifica o INCIDENTE; `natural_key`
-- identifica a ORIGEM do sinal. Um mesmo incidente pode ser visto pelo ADF e
-- pelo Databricks (duas chaves naturais, uma só chave de correlação).
CREATE TABLE tbl_problems (
  problem_id       UNIQUEIDENTIFIER NOT NULL DEFAULT NEWSEQUENTIALID(),
  source           VARCHAR(30)      NOT NULL REFERENCES tbl_ref_source(source),   -- quem detectou primeiro
  correlation_key  NVARCHAR(400)    NOT NULL,
  natural_key      NVARCHAR(400)    NOT NULL,
  key_rule         VARCHAR(24)      NOT NULL,   -- regra que gerou a chave: JOB_ID, RUN_NAME, ADF_ACTIVITY, ADF_PIPELINE, ADF_TRIGGER, IR_NODES, EXPECTATION...
  workspace_id     VARCHAR(50)      NULL,
  domain           VARCHAR(50)      NOT NULL DEFAULT 'DEFAULT',
  incident_status  VARCHAR(10)      NOT NULL CHECK (incident_status IN ('OPEN', 'RESOLVED')),
  severity         VARCHAR(20)      NOT NULL,
  error_code       VARCHAR(50)      NULL,
  error_description  NVARCHAR(1000) NULL,
  opening_run_id   VARCHAR(80)      NULL,
  opened_at        DATETIMEOFFSET(3) NOT NULL,
  closing_run_id   VARCHAR(80)      NULL,
  closed_at        DATETIMEOFFSET(3) NULL,
  occurrence_count      INT         NOT NULL DEFAULT 1,
  opening_notified_at   DATETIMEOFFSET(3) NULL,
  closing_notified_at   DATETIMEOFFSET(3) NULL,
  CONSTRAINT pk_problems PRIMARY KEY (problem_id)
);""",

    r"""
-- A garantia central do desenho: no máximo UM incidente aberto por
-- correlação. O índice é sobre `correlation_key`, não sobre
-- (source, natural_key); indexar por origem permitiria dois incidentes
-- abertos para o mesmo problema visto por duas origens. É este índice que
-- resolve a corrida entre processos: quem perder recebe erro 2601/2627 e
-- passa a incrementar o incidente do vencedor.
CREATE UNIQUE INDEX ux_problems_open
  ON tbl_problems (correlation_key)
  WHERE incident_status = 'OPEN';""",

    r"""
-- Sustenta o painel de incidentes abertos (filtra por severidade e ordena por
-- abertura).
CREATE INDEX ix_problems_open
  ON tbl_problems (incident_status, severity, opened_at)
  WHERE incident_status = 'OPEN';""",

    r"""
-- Sustenta o painel de fechados recentemente (filtra por status e ordena por
-- fechamento).
CREATE INDEX ix_problems_closed
  ON tbl_problems (incident_status, closed_at)
  WHERE incident_status = 'RESOLVED';""",

    r"""
-- Eventos técnicos de um incidente, em qualquer ordem de chegada. O
-- ON DELETE CASCADE é requisito da purga: sem ele, apagar um incidente falha
-- por violação de chave estrangeira.
CREATE TABLE tbl_technical_events (
  event_id           UNIQUEIDENTIFIER NOT NULL DEFAULT NEWSEQUENTIALID(),
  problem_id         UNIQUEIDENTIFIER NOT NULL
                       REFERENCES tbl_problems(problem_id) ON DELETE CASCADE,
  source             VARCHAR(30)      NOT NULL REFERENCES tbl_ref_source(source),
  error_code         VARCHAR(50)      NULL,
  error_description  NVARCHAR(1000)   NULL,
  run_id             VARCHAR(80)      NULL,
  natural_key        NVARCHAR(400)    NULL,
  source_event_id    VARCHAR(200)     NULL,   -- id determinístico do produtor, para idempotência

  -- Id da MESMA execução visto por outra origem: hoje, o ActivityRunId do
  -- ADF. A correlation_key do incidente é estável por processo (para o
  -- sucesso seguinte poder fechar o incidente), então é esta coluna que
  -- liga, depois, o evento do ADF e o evento do Databricks da mesma
  -- execução. Não participa de nenhuma decisão de estado.
  correlation_ref    VARCHAR(80)      NULL,
  registered_at      DATETIMEOFFSET(3) NOT NULL DEFAULT SYSDATETIMEOFFSET(),
  CONSTRAINT pk_technical_events PRIMARY KEY (event_id)
);""",

    r"""
-- Idempotência: reenviar o mesmo evento (após timeout, por exemplo) não gera
-- efeito duas vezes.
CREATE UNIQUE INDEX ux_events_idempotency
  ON tbl_technical_events (source, source_event_id)
  WHERE source_event_id IS NOT NULL;""",

    r"""
CREATE INDEX ix_events_problem ON tbl_technical_events (problem_id);""",

    # =====================================================================
    # 5. Notificação (outbox)
    # =====================================================================

    r"""
-- A intenção de notificar é gravada na MESMA transação que muda o incidente;
-- a entrega acontece em outra etapa. Sem isso, uma queda entre o envio ao
-- Teams e o UPDATE duplicaria ou perderia o alerta.
CREATE TABLE tbl_notification_outbox (
  outbox_id  BIGINT IDENTITY(1, 1) NOT NULL PRIMARY KEY,
  problem_id UNIQUEIDENTIFIER      NOT NULL
               REFERENCES tbl_problems(problem_id) ON DELETE CASCADE,

  -- 'OPEN' e 'CLOSE' são o TIPO DA NOTIFICAÇÃO (avisar que abriu, avisar que
  -- fechou). Não confundir com tbl_problems.incident_status, que usa
  -- 'OPEN'/'RESOLVED' e é o estado do incidente.
  type       VARCHAR(10)       NOT NULL CHECK (type IN ('OPEN', 'CLOSE')),

  created_at     DATETIMEOFFSET(3) NOT NULL DEFAULT SYSDATETIMEOFFSET(),
  sent_at        DATETIMEOFFSET(3) NULL,   -- nulo = pendente
  attempt_count  INT               NOT NULL DEFAULT 0,
  last_error     NVARCHAR(500)     NULL
);""",

    r"""
CREATE INDEX ix_outbox_pending
  ON tbl_notification_outbox (created_at)
  WHERE sent_at IS NULL;""",

    # =====================================================================
    # 6. Views
    # =====================================================================

    r"""
-- Fila lida pela etapa de notificação do coletor: cada notificação pendente
-- com a rota do seu domínio e severidade. Se não houver rota para o domínio,
-- usa a rota 'DEFAULT' da mesma severidade: o alerta nunca some por falta de
-- rota.
CREATE OR ALTER VIEW dbo.vw_pending_notifications AS
SELECT o.outbox_id,
       o.type,
       o.attempt_count,
       p.problem_id,
       p.source,
       p.domain,
       p.severity,
       p.natural_key,
       p.error_code,
       p.error_description,
       p.occurrence_count,
       p.opened_at,
       p.closed_at,
       r.channel,
       r.destination
  FROM tbl_notification_outbox o
  JOIN tbl_problems p
    ON p.problem_id = o.problem_id
 OUTER APPLY (
       SELECT TOP 1 a.channel, a.destination
         FROM tbl_cfg_alert_routing a
        WHERE a.active = 1
          AND a.severity = p.severity
          AND a.domain IN (p.domain, 'DEFAULT')
        ORDER BY CASE WHEN a.domain = p.domain THEN 0 ELSE 1 END
       ) r
 WHERE o.sent_at IS NULL;""",

    r"""
-- Incidentes abertos, para o Power BI (DirectQuery) e para a API. Quem
-- consome lê as views, nunca as tabelas: assim uma mudança interna em
-- tbl_problems não quebra relatório em produção. A descrição do erro vai
-- truncada porque mensagem de erro pode carregar dado de negócio.
CREATE OR ALTER VIEW dbo.vw_open_problems AS
SELECT p.problem_id,
       p.source,
       p.domain,
       p.severity,
       p.natural_key                    AS object,
       p.error_code,
       LEFT(p.error_description, 300)   AS error_description,
       p.occurrence_count,
       p.opened_at,
       DATEDIFF(minute, p.opened_at, SYSDATETIMEOFFSET()) AS open_minutes,
       p.workspace_id,
       p.opening_run_id,
       (SELECT COUNT(*)
          FROM tbl_technical_events e
         WHERE e.problem_id = p.problem_id)                AS event_count
  FROM tbl_problems p
 WHERE p.incident_status = 'OPEN';""",

    r"""
-- Incidentes fechados hoje e ontem (dias UTC), para a retrospectiva do turno.
-- Quem precisar de outra janela filtra em cima de `closed_at`.
CREATE OR ALTER VIEW dbo.vw_recently_closed_problems AS
SELECT p.problem_id,
       p.source,
       p.domain,
       p.severity,
       p.natural_key                    AS object,
       p.error_code,
       LEFT(p.error_description, 300)   AS error_description,
       p.occurrence_count,
       p.opened_at,
       p.closed_at,
       DATEDIFF(minute, p.opened_at, p.closed_at) AS duration_minutes,
       p.workspace_id,
       p.opening_run_id,
       p.closing_run_id,
       (SELECT COUNT(*)
          FROM tbl_technical_events e
         WHERE e.problem_id = p.problem_id)        AS event_count
  FROM tbl_problems p
 WHERE p.incident_status = 'RESOLVED'
   AND p.closed_at >= DATEADD(day, -1, CAST(CAST(SYSDATETIMEOFFSET() AS DATE) AS DATETIMEOFFSET(3)));""",
)
