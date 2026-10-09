"""API pública do log de execução de pipeline (raw + cleansed + curated).

Notebooks e módulos de carga devem importar SOMENTE daqui. O DDL, o schema
Spark e a serialização posicional são detalhes internos: mantê-los privados é o
que garante que as três camadas evoluam a tabela por um único caminho.

Uso recomendado — o logger como SINK do pipeline, gravando em lote:

    from vc_observability.pipeline_logging import EventLogger, probe_audit_sink

    catalogo_it = sf.get_catalog_name("it")
    probe_audit_sink(spark, catalogo_it)          # no INÍCIO do run
    audit = EventLogger(
        spark, catalog=catalogo_it, layer="raw",
        environment="DEV", dbutils=dbutils,
    )
    try:
        pipeline.run(..., sink=audit)             # o pipeline chama audit.emit(dict)
    finally:
        audit.close()                             # descarrega o que sobrou

Uso avulso, quando há um evento só:

    from vc_observability.pipeline_logging import PipelineEvent, build_execution_id

    audit.register(PipelineEvent(
        execution_batch_id=batch_id,
        execution_id=build_execution_id(full_table),
        status="PUBLISHED",
        layer="raw",
        target_table=full_table,
        load_strategy="FULL",
        rows_inserted=21_591_538,
        detail="RECREATED — FULL overwrite",
    ))

Evite ``register()`` num laço: cada chamada é um commit Delta. Um run de 95
tabelas emite 190 eventos — use ``emit``/``close`` ou ``register_many``.
"""

from .contracts import (
    LAYER_CLEANSED,
    LAYER_CURATED,
    LAYER_RAW,
    STATUS_EMPTY_SOURCE,
    STATUS_FAILED,
    STATUS_NOOP,
    STATUS_OPTIMIZED,
    STATUS_PUBLISHED,
    STATUS_RETRY,
    STATUS_SKIPPED,
    STATUS_STARTED,
    STRATEGY_APPEND,
    STRATEGY_DELTA,
    STRATEGY_FULL,
    STRATEGY_SNAPSHOT,
    VALID_STATUSES,
    PipelineEvent,
)
from .ddl import LOG_TABLE_NAME, full_table_name, resolve_default_audit_location
from .logger import (
    DEFAULT_BUFFER_SIZE,
    EventLogger,
    audit_status,
    build_execution_id,
    ensure_log_table,
    format_hms,
    format_table_label,
    log,
    probe_audit_sink,
)

__all__ = [
    "DEFAULT_BUFFER_SIZE",
    "LAYER_CLEANSED",
    "LAYER_CURATED",
    "LAYER_RAW",
    "LOG_TABLE_NAME",
    "STATUS_EMPTY_SOURCE",
    "STATUS_FAILED",
    "STATUS_NOOP",
    "STATUS_OPTIMIZED",
    "STATUS_PUBLISHED",
    "STATUS_RETRY",
    "STATUS_SKIPPED",
    "STATUS_STARTED",
    "STRATEGY_APPEND",
    "STRATEGY_DELTA",
    "STRATEGY_FULL",
    "STRATEGY_SNAPSHOT",
    "VALID_STATUSES",
    "EventLogger",
    "PipelineEvent",
    "audit_status",
    "build_execution_id",
    "ensure_log_table",
    "format_hms",
    "format_table_label",
    "full_table_name",
    "log",
    "probe_audit_sink",
    "resolve_default_audit_location",
]
