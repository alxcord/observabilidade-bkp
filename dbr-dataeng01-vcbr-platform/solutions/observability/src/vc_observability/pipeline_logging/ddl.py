"""DDL e schema Spark do log de execução de pipeline.

Importe este módulo para criar a tabela ou validar o schema. Ele é a ÚNICA
fonte de verdade do DDL: nenhum pipeline deve emitir CREATE/ALTER por conta
própria, senão três camadas passam a evoluir o schema em direções diferentes.
"""

from __future__ import annotations

from collections.abc import Iterable

from pyspark.sql.types import (
    DateType,
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# ── Localização governada ────────────────────────────────────────────────────
# A tabela é EXTERNAL sempre. O ambiente VCBR não tem managed location em
# {env}_it nem em {env}_it.corp_raw (comprovado 2026-08-17 via DESCRIBE SCHEMA
# EXTENDED — Location vazio); tabela managed não tem onde ser materializada e
# todo write falha com UC_CLOUD_STORAGE_ACCESS_FAILURE.
#
# O caminho AGORA é resolvido dentro deste módulo (era responsabilidade do
# notebook chamador antes de 2026-08-17, o que espalhava hardcode em cada
# ponto de composição). O mapping abaixo é a única fonte de verdade —
# atualizar aqui quando um ambiente novo entrar. Testado em 2026-08-17 com
# CREATE + INSERT + DESCRIBE em dev_it.corp_raw.
#
# Nome pela fórmula 2.1 do Guia VCBR: tipagem_origem_dominio_nome_amigavel
#   tbl  = tabela física
#   dbr  = origem Databricks (o log é PRODUZIDO pelo Databricks, não pelo SAP)
#   it   = domínio TI
# Schema corp_raw: o log é registro operacional bruto (append-only), não sofre
# transformação de qualidade — vive semanticamente em raw, embora seja gerado
# no Databricks e não venha de um source externo. Convenção alinhada com Diego
# 2026-08-17.
# DIVERGÊNCIA CONHECIDA (item 2.6): as COLUNAS estão em inglês, não em português
# com prefixo semântico (dh_inicio, qt_linhas_inseridas...). É tabela técnica de
# infraestrutura, consumida por código e não por negócio, e o restante dos
# módulos é em inglês. Pendente de decisão formal da governança — se a decisão
# for português, renomear via ALTER TABLE RENAME COLUMN (barato em append-only).
LOG_SCHEMA = "corp_raw"
LOG_TABLE_NAME = "tbl_dbr_it_log_execucao_pipeline"

# Mapping ENV → storage account para o container `raw` do datalake.
# NUNCA usar o segmento literal 'vol' no path: UC bloqueia PATH_CREATE_TABLE
# achando que é Volume UC (observado 2026-08-17 em .../IT/vol/...).
_STORAGE_ACCT_BY_ENV: dict[str, str] = {
    "DEV": "sadatalakedevvcbr",
    "PRD": "sadatalakevcbr",
}


def resolve_default_audit_location(environment: str) -> str:
    """Resolve o LOCATION canônico da tabela de log por ambiente.

    Uma fonte de verdade só. Notebooks chamadores devem passar ``location=None``
    para EventLogger (ou omitir), deixando a resolução aqui. Quem precisar
    override para experimento passa ``location=<caminho>`` explícito.
    """
    key = (environment or "").strip().upper()
    account = _STORAGE_ACCT_BY_ENV.get(key)
    if account is None:
        raise ValueError(
            f"Ambiente sem storage account mapeado para a tabela de log: "
            f"{environment!r}. Registrar em _STORAGE_ACCT_BY_ENV."
        )
    return f"abfss://raw@{account}.dfs.core.windows.net/IT/PipelineLog/{LOG_TABLE_NAME}"


# Ordem POSICIONAL — acoplada a PipelineEvent.as_row(). Alterar as duas juntas.
#
# COLUNA NOVA ENTRA NO FIM, sempre. `ALTER TABLE ADD COLUMNS` acrescenta ao
# final do schema das tabelas que já existem; inserir no meio desta tupla faria
# a ordem do CREATE divergir da ordem das tabelas já criadas em produção.
LOG_COLUMNS: tuple[str, ...] = (
    "execution_batch_id",
    "execution_id",
    "event_seq",
    "environment",
    "layer",
    "domain",
    "target_table",
    "source_system",
    "source_object",
    "load_strategy",
    "started_at",
    "ended_at",
    "duration_seconds",
    "queue_wait_seconds",
    "dt_execution",
    "window_start",
    "window_end",
    "status",
    "attempts",
    "error_type",
    "error_message",
    "detail",
    "rows_source",
    "rows_inserted",
    "rows_updated",
    "job_run_id",
    "task_run_id",
    "orchestrator_run_id",
    "notebook_path",
    "metrics_json",
    # ── Acrescentadas em 2026-08-20 ──
    "status_detail",
    "window_start_at",
    "window_end_at",
)

LOG_SPARK_SCHEMA = StructType(
    [
        StructField("execution_batch_id", StringType()),
        StructField("execution_id", StringType()),
        StructField("event_seq", IntegerType()),
        StructField("environment", StringType()),
        StructField("layer", StringType()),
        StructField("domain", StringType()),
        StructField("target_table", StringType()),
        StructField("source_system", StringType()),
        StructField("source_object", StringType()),
        StructField("load_strategy", StringType()),
        StructField("started_at", TimestampType()),
        StructField("ended_at", TimestampType()),
        StructField("duration_seconds", DoubleType()),
        StructField("queue_wait_seconds", DoubleType()),
        StructField("dt_execution", DateType()),
        StructField("window_start", DateType()),
        StructField("window_end", DateType()),
        StructField("status", StringType()),
        StructField("attempts", IntegerType()),
        StructField("error_type", StringType()),
        StructField("error_message", StringType()),
        StructField("detail", StringType()),
        StructField("rows_source", LongType()),
        StructField("rows_inserted", LongType()),
        StructField("rows_updated", LongType()),
        StructField("job_run_id", StringType()),
        StructField("task_run_id", StringType()),
        StructField("orchestrator_run_id", StringType()),
        StructField("notebook_path", StringType()),
        StructField("metrics_json", StringType()),
        StructField("status_detail", StringType()),
        StructField("window_start_at", TimestampType()),
        StructField("window_end_at", TimestampType()),
    ]
)

# COMMENT estruturado obrigatório em Cleansed/Curated (item 2.9 do Guia VCBR).
# Campos sem informação disponível ficam como [PREENCHER].
# O separador é "\n\n" (quebra de linha dupla REAL). Escrever "\n\n" gravaria
# a sequência literal barra-n barra-n dentro do COMMENT, que não é o que o
# item 2.9 pede e quebra a leitura no Catalog Explorer.
_TABLE_COMMENT = (
    "Propósito: registrar, em formato append-only, cada operação de carga "
    "executada nas camadas raw, cleansed e curated, permitindo rastrear duração, "
    "volume, tentativas e falhas sem depender do log do driver."
    "\n\n"
    "Grão: um evento por operação por tabela por camada por tentativa "
    "(execution_id + event_seq)."
    "\n\n"
    "Origem: módulo vc_observability.pipeline_logging, invocado pelos notebooks de "
    "carga de cada camada."
    "\n\n"
    "Período: a partir da primeira execução instrumentada; retenção controlada "
    "por VACUUM padrão de 168 horas."
    "\n\n"
    "Relacionamentos: target_table referencia a tabela física carregada; "
    "job_run_id e task_run_id referenciam a execução no Databricks Jobs; "
    "orchestrator_run_id referencia o run do Azure Data Factory."
    "\n\n"
    "PK: execution_id + event_seq"
    "\n\n"
    "FK: [PREENCHER]"
    "\n\n"
    "Observações: tabela append-only — correções entram como evento novo, nunca "
    "como update. STARTED é o único status não-terminal e sempre tem event_seq=1 "
    "com ended_at nulo; para obter o resultado final de uma execução, filtrar "
    "event_seq > 1. A coluna status usa vocabulário genérico às três camadas; "
    "status_detail traz o refinamento específico da camada quando existe. "
    "Colunas window_* são nulas quando a carga não usa janela temporal (FULL ou "
    "full_reload); window_start_at e window_end_at trazem o instante exato do "
    "corte, e window_start e window_end a data correspondente."
    "\n\n"
    "Owner: [PREENCHER]"
    "\n\n"
    "Owner_email: [PREENCHER]"
    "\n\n"
    "Steward: [PREENCHER]"
    "\n\n"
    "Steward_email: [PREENCHER]"
)

_COLUMN_COMMENTS: dict[str, str] = {
    "execution_batch_id": "UUID do run — agrupa todas as tabelas de um mesmo disparo",
    "execution_id": "<target_table>_<yyyyMMddHHmmss do inicio real>",
    "event_seq": "Ordem do evento na execucao (1=STARTED, N=terminal)",
    "environment": "DEV | PRD",
    "layer": "raw | cleansed | curated",
    "domain": "Glossario controlado: pro, fin, log, mdm, com, ind, tax, ctrl, it, sus, hr, qlty, eng",
    "target_table": "FQN completo da tabela carregada: catalog.schema.table",
    "source_system": "Sistema de origem, mesmo vocabulario de vc_source_system: ds, bw, sql, postgres, api, flat_file, databricks",
    "source_object": "Objeto de origem — cds_view na raw, FQN da tabela nas demais camadas",
    "load_strategy": "FULL | DELTA | APPEND | SNAPSHOT",
    "started_at": "Inicio real da operacao, horario do host (naive)",
    "ended_at": "Fim da operacao, horario do host (naive). NULL em STARTED",
    "duration_seconds": "ended_at - started_at em segundos. NULL em STARTED",
    "queue_wait_seconds": "Espera entre enfileirar e iniciar de fato, em segundos",
    "dt_execution": "Data do inicio — eixo de clustering para consultas por dia",
    "window_start": "Data de inicio da janela processada. NULL quando nao se aplica",
    "window_end": "Data de fim da janela processada. NULL quando nao se aplica",
    "status": "Vocabulario generico as tres camadas: STARTED | PUBLISHED | NOOP | RETRY | FAILED | EMPTY_SOURCE | SKIPPED | OPTIMIZED",
    "attempts": "Numero da tentativa — 1 na primeira execucao",
    "error_type": "Classe da excecao, para agrupamento por padrao de falha. NULL em sucesso",
    "error_message": "Mensagem ou stack truncada em 2000 caracteres. NULL em sucesso",
    "detail": "Mensagem de resultado legivel por humano",
    "rows_source": "Linhas lidas da origem",
    "rows_inserted": "Linhas inseridas no destino",
    "rows_updated": "Linhas atualizadas no destino",
    "job_run_id": "Databricks job run id — link direto para o Spark UI da execucao",
    "task_run_id": "Databricks task run id",
    "orchestrator_run_id": "Run id do Azure Data Factory que disparou a execucao",
    "notebook_path": "Caminho completo do notebook no workspace",
    "metrics_json": "JSON livre por camada: read_path, predicates, throughput, execution_mode",
    "status_detail": "Refinamento do status dentro da camada: FIRST_LOAD, REJECTED, SKIPPED_PREFLIGHT_NOOP, SKIPPED_EMPTY_WINDOW",
    "window_start_at": "Instante exato de inicio da janela. NULL quando nao se aplica",
    "window_end_at": "Instante exato de fim da janela. NULL quando nao se aplica",
}

# Tipo SQL por coluna, derivado do schema Spark — usado no CREATE e nas
# migrações aditivas, garantindo que as duas nunca divirjam.
_SQL_TYPES = {
    StringType(): "STRING",
    IntegerType(): "INT",
    LongType(): "BIGINT",
    DoubleType(): "DOUBLE",
    DateType(): "DATE",
    TimestampType(): "TIMESTAMP",
}


def _sql_type(field: StructField) -> str:
    """Return the SQL type name for one schema field."""
    return _SQL_TYPES[field.dataType]


def _escape(text: str) -> str:
    """Escape single quotes for safe inclusion in a SQL string literal."""
    return text.replace("'", "''")


def full_table_name(catalog: str) -> str:
    """Return the fully qualified log table for one environment catalog."""
    return f"{catalog}.{LOG_SCHEMA}.{LOG_TABLE_NAME}"


def build_create_statement(catalog: str, location: str | None = None) -> str:
    """Build the idempotent CREATE TABLE for the pipeline execution log.

    Liquid Clustering em (dt_execution, layer, target_table): as três dimensões
    de filtro reais. Não usar particionamento — LC é incompatível com partition
    e resolve melhor a assimetria entre camadas.

    ``location`` informado produz uma tabela EXTERNAL nesse caminho; omitido,
    mantém o comportamento gerenciado anterior. O default preserva quem já tem
    managed location configurada — este parâmetro não quebra ninguém.
    """
    columns = ",\n            ".join(
        f"{field.name:<20} {_sql_type(field):<9} "
        f"COMMENT '{_escape(_COLUMN_COMMENTS.get(field.name, ''))}'"
        for field in LOG_SPARK_SCHEMA.fields
    )
    location_clause = (
        f"LOCATION '{_escape(location.strip())}'"
        if location and location.strip()
        else ""
    )
    return f"""
        CREATE TABLE IF NOT EXISTS {full_table_name(catalog)} (
            {columns}
        )
        USING DELTA
        CLUSTER BY (dt_execution, layer, target_table)
        {location_clause}
        TBLPROPERTIES (
            'delta.enableChangeDataFeed' = 'true',
            'delta.enableDeletionVectors' = 'true'
        )
        COMMENT '{_escape(_TABLE_COMMENT)}'
    """


def build_add_column_statements(catalog: str) -> list[str]:
    """Build additive ALTER statements for EVERY column in the schema.

    Migração é sempre ADITIVA: coluna nova entra com NULL nos eventos antigos.
    Nunca remover nem alterar tipo — o histórico do log é evidência e uma
    reescrita destrutiva apaga justamente o que se queria auditar.

    Prefira ``pending_column_statements`` no caminho quente: esta função emite
    um ALTER por coluna do schema, e todos falham depois da primeira criação.
    """
    return _alter_statements(catalog, [f.name for f in LOG_SPARK_SCHEMA.fields])


def pending_column_statements(
    catalog: str, existing_columns: Iterable[str]
) -> list[str]:
    """Build ALTER statements only for columns the table does not have yet.

    Recebe os nomes existentes em vez de consultar o Spark: mantém este módulo
    puro e testável. Sem isto, todo run dispara um ALTER por coluna do schema —
    hoje são 33 statements por execução, todos falhando, num round-trip de
    metastore cada.
    """
    have = {str(name).lower() for name in existing_columns}
    missing = [f.name for f in LOG_SPARK_SCHEMA.fields if f.name.lower() not in have]
    return _alter_statements(catalog, missing)


def _alter_statements(catalog: str, column_names: Iterable[str]) -> list[str]:
    """Render one additive ALTER per requested column, in schema order."""
    table = full_table_name(catalog)
    wanted = {str(name) for name in column_names}
    return [
        f"ALTER TABLE {table} ADD COLUMNS "
        f"({field.name} {_sql_type(field)} "
        f"COMMENT '{_escape(_COLUMN_COMMENTS.get(field.name, ''))}')"
        for field in LOG_SPARK_SCHEMA.fields
        if field.name in wanted
    ]
