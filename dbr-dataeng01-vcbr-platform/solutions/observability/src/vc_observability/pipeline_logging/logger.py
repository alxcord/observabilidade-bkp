"""Persistência de eventos e log de console para pipelines de todas as camadas.

Importe ``EventLogger`` do pacote raiz. A tabela Delta é criada de forma
preguiçosa no primeiro evento; as funções de formatação são puras e seguras
para chamada concorrente a partir de threads do driver.

ESCRITA EM LOTE — leia antes de mexer:
``register()`` grava UM commit Delta por evento. Uma carga de 95 tabelas emite
190 eventos (STARTED + terminal), ou seja 190 transações e 190 arquivos
minúsculos por run; diariamente, ~69 mil commits por ano numa tabela que
ninguém otimiza. Por isso existe ``register_many`` (um commit para N eventos) e
o par ``emit``/``flush``, que acumula e descarrega em blocos. O caminho
preferido é ``emit`` durante a carga + ``close()`` no fim.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from threading import Lock
from typing import Any

from .contracts import (
    STATUS_SKIPPED,
    STATUS_STARTED,
    PipelineEvent,
)
from .ddl import (
    LOG_SPARK_SCHEMA,
    build_create_statement,
    full_table_name,
    pending_column_statements,
    resolve_default_audit_location,
)

_print_lock = Lock()

# Audit sink health — module-level flags so that probe_audit_sink and
# every EventLogger instance share the same state within one process.
# São de MÓDULO, não de instância: o cluster é compartilhado e o processo
# Python sobrevive entre runs, então `probe_audit_sink` precisa reabilitar
# no início de cada run — senão uma falha transitória de ontem deixaria a
# auditoria muda para sempre.
_AUDIT_ENABLED: bool = True
_AUDIT_SUPPRESSED: int = 0
_AUDIT_DISABLE_REASON: str = ""
# As flags são lidas e escritas por threads do driver (as filas paralelas da
# ingestão). `_AUDIT_SUPPRESSED += 1` não é atômico: sem o lock o contador
# perde eventos justamente no cenário em que ele importa.
_audit_lock = Lock()

# Tamanho padrão do buffer de ``emit``. 50 mantém a perda máxima em caso de
# queda abaixo de um bloco e reduz 190 commits para 4 num run de 95 tabelas.
DEFAULT_BUFFER_SIZE = 50

# Valor sentinela da sonda. Fica em execution_batch_id porque é a primeira
# coluna do schema e a única que a sonda precisa poder filtrar no DELETE.
_PROBE_BATCH_ID = "__audit_probe__"


def _disable_audit(reason: str, suppressed: int = 0) -> None:
    """Turn the sink off for the rest of the run, atomically."""
    global _AUDIT_ENABLED, _AUDIT_SUPPRESSED, _AUDIT_DISABLE_REASON
    with _audit_lock:
        _AUDIT_ENABLED = False
        _AUDIT_DISABLE_REASON = reason
        _AUDIT_SUPPRESSED += suppressed


def _count_suppressed(amount: int) -> None:
    """Increment the suppressed-event counter atomically."""
    global _AUDIT_SUPPRESSED
    with _audit_lock:
        _AUDIT_SUPPRESSED += amount


def ensure_log_table(spark, catalog: str, location: str | None = None) -> None:
    """Create the log table and apply additive column migrations idempotently.

    Extraído de ``EventLogger.ensure_table`` para que a sonda possa garantir a
    tabela ANTES de escrever. Sem isso, uma sonda que escrevesse primeiro
    criaria a tabela com o schema errado e o CREATE IF NOT EXISTS seguinte
    viraria no-op — a tabela nasceria permanentemente inválida.

    ``location`` cria a tabela como EXTERNAL nesse caminho. Só tem efeito na
    PRIMEIRA criação: numa tabela já existente o CREATE IF NOT EXISTS é no-op e
    o LOCATION é ignorado. Converter gerenciada em external exige DROP.
    """
    table = full_table_name(catalog)
    spark.sql(build_create_statement(catalog, location))

    # Evolução aditiva: tabelas criadas por versões anteriores do módulo
    # recebem as colunas novas. Perguntar quais faltam custa uma leitura de
    # metadado e evita disparar um ALTER por coluna do schema em todo run.
    try:
        existing = spark.table(table).columns
    except Exception:
        existing = []
    for statement in pending_column_statements(catalog, existing):
        try:
            spark.sql(statement)
        except Exception as error:
            # Coluna já existente é o caso benigno; permissão negada não é.
            # Sem esta linha, os dois casos ficavam mudos do mesmo jeito.
            log(f"AUDIT_MIGRATION_SKIPPED | {type(error).__name__}: {str(error)[:160]}")


def probe_audit_sink(spark, catalog: str, location: str | None = None) -> None:
    """Single-shot probe at run start: write + delete a sentinel row.

    Um ``DESCRIBE`` responde pelo metastore e nunca toca o storage — não
    detecta falha de credencial nem de managed location. A sonda escreve de
    verdade, via Delta, para exercitar o caminho inteiro.

    A linha sentinela usa ``LOG_SPARK_SCHEMA``, o mesmo schema dos eventos
    reais: escrever um DataFrame de schema diferente falharia no append
    (dando falso negativo) ou, pior, evoluiria o schema da tabela com uma
    coluna de teste.

    Chame no INÍCIO do run. Descobrir que o sink não grava depois da carga
    inteira significa perder todos os eventos dela.

    Nunca levanta. Em caso de falha, as flags de módulo desligam a auditoria
    e todo ``register()`` seguinte vira no-op pelo resto do run.
    """
    global _AUDIT_ENABLED, _AUDIT_SUPPRESSED, _AUDIT_DISABLE_REASON
    # Reabilita no início do run: o estado anterior é de outro run.
    with _audit_lock:
        _AUDIT_ENABLED = True
        _AUDIT_SUPPRESSED = 0
        _AUDIT_DISABLE_REASON = ""

    table = full_table_name(catalog)
    started = dt.datetime.now()
    probe_event = PipelineEvent(
        execution_batch_id=_PROBE_BATCH_ID,
        execution_id=_PROBE_BATCH_ID,
        status=STATUS_SKIPPED,
        layer="raw",
        target_table=table,
        event_seq=2,
        started_at=started,
        ended_at=started,
        detail="sonda de gravabilidade do sink de auditoria",
    )
    try:
        ensure_log_table(spark, catalog, location)
        (
            spark.createDataFrame(
                [probe_event.as_row(dt_execution=started.date())],
                LOG_SPARK_SCHEMA,
            )
            .write.mode("append")
            .saveAsTable(table)
        )
        spark.sql(f"DELETE FROM {table} WHERE execution_batch_id = '{_PROBE_BATCH_ID}'")
    except Exception as error:
        # Intencional — a sonda existe para diagnosticar, não para derrubar.
        _disable_audit(f"{type(error).__name__}: {str(error)[:200]}")
        log(
            "AUDIT_DISABLED | sink de auditoria não gravável — o run continua "
            "SEM registrar eventos | "
            f"tabela={table} | causa={_AUDIT_DISABLE_REASON}"
        )


def audit_status() -> dict:
    """Return a lightweight dict for the run payload and SUMMARY line."""
    with _audit_lock:
        return {
            "audit_enabled": _AUDIT_ENABLED,
            "audit_events_suppressed": _AUDIT_SUPPRESSED,
            "audit_disable_reason": _AUDIT_DISABLE_REASON,
        }


def format_hms(seconds: float | None) -> str:
    """Format elapsed seconds as ``HH:MM:SS``."""
    return str(dt.timedelta(seconds=max(int(seconds or 0), 0)))


def log(message: str) -> None:
    """Print one timestamped line atomically across driver worker threads."""
    with _print_lock:
        print(f"[{dt.datetime.now():%H:%M:%S}] {message}")


def format_table_label(environment: str, target_table: str) -> str:
    """Return one consistent environment-qualified label for human output."""
    prefix = f"{environment} | " if environment else ""
    return f"{prefix}{target_table}"


def build_execution_id(target_table: str, started_at: dt.datetime | None = None) -> str:
    """Build a stable execution id anchored to the REAL start instant.

    Ancorar no início real (e não na data de referência da janela) é deliberado:
    o id precisa bater com ``started_at`` para que alguém consiga correlacionar
    um evento do log com o run do Databricks sem fazer conta de fuso.
    """
    moment = started_at or dt.datetime.now()
    short_name = target_table.rsplit(".", 1)[-1]
    return f"{short_name}_{moment:%Y%m%d%H%M%S}"


def resolve_databricks_run_context(dbutils=None) -> dict[str, str]:
    """Best-effort extraction of Databricks job/task run ids and notebook path.

    Envolvido em try/except porque o contexto não existe em execução interativa
    nem em teste local — e ausência de contexto nunca pode impedir o log.
    """
    context: dict[str, str] = {"job_run_id": "", "task_run_id": "", "notebook_path": ""}
    if dbutils is None:
        return context
    try:
        tags = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
        context["notebook_path"] = str(tags.notebookPath().getOrElse(lambda: "") or "")
        context["job_run_id"] = str(
            tags.tags().get("multitaskParentRunId").getOrElse(lambda: "") or ""
        )
        context["task_run_id"] = str(
            tags.tags().get("runId").getOrElse(lambda: "") or ""
        )
    except Exception:
        pass
    return context


class EventLogger:
    """Append pipeline events to the governed Delta log for one environment.

    Uma instância serve uma camada. O catálogo é sempre o de TI ({env}_it),
    independentemente do catálogo de destino da carga: o log é centralizado
    para permitir consulta cross-área sem UNION.

    Três formas de registrar, da preferida para a excepcional:

    * ``emit(dict)`` durante a carga + ``close()`` no fim — o pipeline injeta a
      instância como sink e nem precisa conhecer ``PipelineEvent``.
    * ``register_many(eventos)`` — um commit para todos os eventos de uma vez.
    * ``register(evento)`` — um commit para um evento. Só para caso avulso.
    """

    def __init__(
        self,
        spark,
        *,
        catalog: str,
        layer: str,
        environment: str = "",
        dbutils=None,
        orchestrator_run_id: str = "",
        location: str | None = None,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
    ) -> None:
        self.spark = spark
        self.catalog = catalog
        self.layer = layer
        # Caminho ADLS da tabela EXTERNAL. Se o chamador não passar, resolve o
        # default canônico via ddl.resolve_default_audit_location(env) — que é
        # a única fonte de verdade do path (por ambiente). Antes de 2026-08-17
        # o default era None (tabela managed), o que falhava com
        # UC_CLOUD_STORAGE_ACCESS_FAILURE em {env}_it sem managed location.
        self.location = (
            location if location else resolve_default_audit_location(environment)
        )
        self.environment = environment
        self.orchestrator_run_id = orchestrator_run_id
        self.buffer_size = max(int(buffer_size), 1)
        self._context = resolve_databricks_run_context(dbutils)
        self._ensured = False
        self._ensure_lock = Lock()
        self._buffer: list[PipelineEvent] = []
        self._buffer_lock = Lock()

    @property
    def table(self) -> str:
        """Return the fully qualified pipeline log table."""
        return full_table_name(self.catalog)

    def ensure_table(self) -> None:
        """Create the append-only event table idempotently, once per instance.

        O lock evita que oito threads disparem CREATE simultâneos no primeiro
        evento do run — inofensivo por causa do IF NOT EXISTS, mas desperdiça
        commits e polui o histórico Delta.
        """
        if self._ensured:
            return
        with self._ensure_lock:
            if self._ensured:
                return
            ensure_log_table(self.spark, self.catalog, self.location)
            self._ensured = True

    # ── Entrada preferida: buffer ───────────────────────────────────────────

    def emit(self, event: Mapping[str, Any] | PipelineEvent) -> None:
        """Accept one event as dict (or PipelineEvent) and buffer it.

        Assinatura pensada para ser injetada como *sink* num pipeline: o
        chamador emite dicionários e não precisa importar ``PipelineEvent``.
        Descarrega sozinho a cada ``buffer_size`` eventos; ``close()`` fecha o
        resto. Nunca levanta.
        """
        try:
            prepared = self._prepare(
                event
                if isinstance(event, PipelineEvent)
                else PipelineEvent.from_mapping(event)
            )
        except Exception as error:
            log(f"AUDIT_EVENT_INVALID | {type(error).__name__}: {str(error)[:160]}")
            return

        self._echo(prepared)
        with self._buffer_lock:
            self._buffer.append(prepared)
            pending = len(self._buffer)
        if pending >= self.buffer_size:
            self.flush()

    def flush(self) -> None:
        """Persist everything buffered so far in a single commit."""
        with self._buffer_lock:
            pending, self._buffer = self._buffer, []
        if pending:
            self._write(pending)

    def close(self) -> None:
        """Flush the buffer. Safe to call more than once."""
        self.flush()

    # ── Entradas explícitas ─────────────────────────────────────────────────

    def register_many(
        self, events: Sequence[PipelineEvent], *, echo: bool = True
    ) -> None:
        """Persist N events in ONE Delta commit.

        É a diferença entre 1 e 190 transações num run de 95 tabelas.
        """
        prepared = [self._prepare(event) for event in events]
        if echo:
            for event in prepared:
                self._echo(event)
        if prepared:
            self._write(prepared)

    def register(self, event: PipelineEvent, *, echo: bool = True) -> None:
        """Persist one immutable event and optionally print its console line.

        Falha ao gravar o log NUNCA propaga: observabilidade não pode derrubar
        a carga que observa. O erro vai para o console para não sumir em silêncio.
        """
        prepared = self._prepare(event)
        if echo:
            self._echo(prepared)
        self._write([prepared])

    # ── Internos ────────────────────────────────────────────────────────────

    def _prepare(self, event: PipelineEvent) -> PipelineEvent:
        """Fill in what the logger knows and the caller left blank."""
        event.layer = event.layer or self.layer
        event.environment = event.environment or self.environment
        event.orchestrator_run_id = (
            event.orchestrator_run_id or self.orchestrator_run_id
        )
        event.job_run_id = event.job_run_id or self._context["job_run_id"]
        event.task_run_id = event.task_run_id or self._context["task_run_id"]
        event.notebook_path = event.notebook_path or self._context["notebook_path"]
        if event.started_at is None:
            event.started_at = dt.datetime.now()
        if event.is_terminal and event.ended_at is None:
            event.ended_at = dt.datetime.now()
        return event

    def _write(self, events: Iterable[PipelineEvent]) -> None:
        """Append a batch of prepared events, never propagating failure.

        O short-circuit de auditoria desligada vem DEPOIS do echo (feito por
        quem chama) de propósito: quando o sink está fora, o console é a única
        observabilidade que resta.
        """
        batch = list(events)
        if not batch:
            return
        if not _AUDIT_ENABLED:
            _count_suppressed(len(batch))
            return

        try:
            self.ensure_table()
            rows = [e.as_row(dt_execution=e.started_at.date()) for e in batch]
            (
                self.spark.createDataFrame(rows, LOG_SPARK_SCHEMA)
                .write.mode("append")
                .saveAsTable(self.table)
            )
        except Exception as error:
            # Intencional — audit failure must never propagate to the pipeline.
            # If the probe passed but a later write fails (e.g. transient storage
            # outage), disable audit for the remainder of the run so we do not
            # repeat the same failure for every table.
            _disable_audit(str(error)[:200], suppressed=len(batch))
            first = batch[0].execution_id
            log(
                f"AUDIT_WRITE_FAILED | lote={len(batch)} | a partir de {first} | "
                f"{type(error).__name__}: {error}"
            )
            log(f"AUDIT_DISABLED | {audit_status()['audit_disable_reason']}")

    def _echo(self, event: PipelineEvent) -> None:
        """Print the human-readable console line for one event."""
        label = format_table_label(event.environment, event.target_table)
        status = event.status
        # status_detail é o que distingue FIRST_LOAD de PUBLISHED e a origem de
        # um SKIPPED. No console ele vale mais que a versão genérica.
        if event.status_detail and event.status_detail != event.status:
            status = f"{event.status_detail}"
        suffix = ""
        if event.status != STATUS_STARTED:
            suffix = f" | duration={format_hms(event.duration_seconds)}"
            # queue_wait separa "a tabela é lenta" de "a tabela esperou a fila":
            # problemas diferentes, com donos diferentes. Abaixo de 1s é ruído.
            if (event.queue_wait_seconds or 0) >= 1:
                suffix += f" | queue_wait={format_hms(event.queue_wait_seconds)}"
        log(f"{status} | {label} | {event.detail}{suffix}")
