"""Contratos do log de execução de pipeline (raw + cleansed + curated).

Importe este módulo para declarar eventos ou validar status.  Ele não acessa
Spark, Delta ou filesystem: é puro e seguro para uso em threads do driver.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any

# ── Camadas -------------------------------------------------------------------
LAYER_RAW = "raw"
LAYER_CLEANSED = "cleansed"
LAYER_CURATED = "curated"
VALID_LAYERS = frozenset({LAYER_RAW, LAYER_CLEANSED, LAYER_CURATED})

# ── Status --------------------------------------------------------------------
# Vocabulário GENÉRICO às três camadas — e é para continuar pequeno. Refinamento
# específico de uma camada (FIRST_LOAD, SKIPPED_PREFLIGHT_NOOP...) vai em
# ``status_detail``, não aqui: cada status novo neste conjunto obriga toda
# consulta cross-camada a conhecê-lo.
# STARTED é o único evento não-terminal: sempre tem event_seq=1 e ended_at NULL.
STATUS_STARTED = "STARTED"
STATUS_PUBLISHED = "PUBLISHED"
STATUS_NOOP = "NOOP"
STATUS_RETRY = "RETRY"
STATUS_FAILED = "FAILED"
STATUS_EMPTY_SOURCE = "EMPTY_SOURCE"
STATUS_SKIPPED = "SKIPPED"
STATUS_OPTIMIZED = "OPTIMIZED"  # manutenção (OPTIMIZE/VACUUM), não carga
VALID_STATUSES = frozenset(
    {
        STATUS_STARTED,
        STATUS_PUBLISHED,
        STATUS_NOOP,
        STATUS_RETRY,
        STATUS_FAILED,
        STATUS_EMPTY_SOURCE,
        STATUS_SKIPPED,
        STATUS_OPTIMIZED,
    }
)

# ── Estratégias de carga ------------------------------------------------------
# Vocabulário deliberadamente genérico às três camadas. SCD2 não faz parte do
# modelo: nenhuma camada historiza por versão de linha.
STRATEGY_FULL = "FULL"  # substitui o conteúdo inteiro do destino
STRATEGY_DELTA = "DELTA"  # MERGE por chave dentro de uma janela
STRATEGY_APPEND = "APPEND"  # acréscimo sem chave (log, evento)
STRATEGY_SNAPSHOT = "SNAPSHOT"  # foto pontual materializada
VALID_STRATEGIES = frozenset(
    {
        STRATEGY_FULL,
        STRATEGY_DELTA,
        STRATEGY_APPEND,
        STRATEGY_SNAPSHOT,
    }
)

MAX_DETAIL_CHARS = 2000
MAX_ERROR_CHARS = 2000
MAX_STATUS_DETAIL_CHARS = 60


def _coerce_date(value: Any) -> dt.date | None:
    """Normalize str/date/datetime/None into a date, never raising.

    Um log que estoura exceção derruba a carga que ele deveria apenas observar,
    então toda coerção aqui é tolerante por decisão de projeto.
    """
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _coerce_datetime(value: Any) -> dt.datetime | None:
    """Normalize str/date/datetime/None into a naive datetime, never raising."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min)
    try:
        parsed = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _truncate(value: str | None, limit: int) -> str | None:
    """Cut free text to the column limit, preserving None."""
    if value is None:
        return None
    text = str(value)
    return text[:limit] if len(text) > limit else text


@dataclass
class PipelineEvent:
    """Um evento imutável de execução de pipeline.

    Um evento representa UMA operação sobre UMA tabela em UMA camada. A tabela
    é append-only: correções entram como evento novo, nunca como update.

    ``target_table`` é sempre o FQN completo (``catalog.schema.table``) porque é
    a única chave estável entre camadas — nome curto colide entre catálogos.
    """

    # ── Identidade ──
    execution_batch_id: str
    execution_id: str
    status: str
    layer: str
    target_table: str

    event_seq: int = 1

    # Refinamento do status DENTRO de uma camada: FIRST_LOAD, REJECTED,
    # SKIPPED_PREFLIGHT_NOOP... Fica fora de VALID_STATUSES de propósito — quem
    # consulta as três camadas filtra por ``status``; quem investiga uma camada
    # específica desce para cá.
    status_detail: str = ""

    # ── Onde ──
    environment: str = ""
    domain: str = ""

    # ── O quê ──
    source_system: str = ""
    source_object: str = ""
    load_strategy: str = ""

    # ── Quando ──
    started_at: dt.datetime | None = None
    ended_at: dt.datetime | None = None
    duration_seconds: float | None = None
    queue_wait_seconds: float | None = None

    # ── Janela: NULL quando não se aplica (FULL/full_reload) ──
    # As colunas DATE existem desde a primeira versão e continuam preenchidas.
    # As colunas *_at guardam o instante exato: com janela ancorada em control
    # point, o corte tem hora e a data sozinha não explica o volume lido.
    # Informar só *_at basta — as DATE são derivadas em ``as_row``.
    window_start: Any = None
    window_end: Any = None
    window_start_at: Any = None
    window_end_at: Any = None

    # ── Resultado ──
    attempts: int = 1
    error_type: str | None = None
    error_message: str | None = None
    detail: str = ""

    # ── Volumes ──
    rows_source: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0

    # ── Rastreabilidade ──
    job_run_id: str = ""
    task_run_id: str = ""
    orchestrator_run_id: str = ""
    notebook_path: str = ""

    # ── Extensão por camada ──
    metrics: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> PipelineEvent:
        """Build an event from a plain dict, ignoring unknown keys.

        É o que permite um pipeline emitir eventos como dicionário sem importar
        esta classe — o acoplamento fica em UMA função, aqui, e chave a mais no
        dict do chamador não quebra nada.
        """
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in values.items() if k in known})

    def validate(self) -> None:
        """Raise ValueError on a contract violation the caller can fix.

        Chamado apenas em testes e no ponto de composição; o logger em runtime
        normaliza em vez de rejeitar, para nunca derrubar a carga observada.
        """
        if self.layer not in VALID_LAYERS:
            raise ValueError(f"layer inválida: {self.layer!r}")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"status inválido: {self.status!r}")
        if self.load_strategy and self.load_strategy not in VALID_STRATEGIES:
            raise ValueError(f"load_strategy inválida: {self.load_strategy!r}")
        if self.target_table.count(".") != 2:
            raise ValueError(
                f"target_table deve ser FQN catalog.schema.table: {self.target_table!r}"
            )

    @property
    def is_terminal(self) -> bool:
        """Return whether this event closes an execution attempt."""
        return self.status != STATUS_STARTED

    def metrics_json(self) -> str | None:
        """Serialize the free-form per-layer metrics, or None when empty."""
        if not self.metrics:
            return None
        try:
            return json.dumps(dict(self.metrics), ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return None

    def as_row(self, *, dt_execution: dt.date) -> tuple:
        """Return the positional tuple matching LOG_COLUMNS in ddl.py.

        A ordem é acoplada a ``ddl.LOG_COLUMNS`` de propósito: as duas listas
        vivem no mesmo módulo e mudam juntas, evitando o custo de montar dict
        por evento em caminho quente.

        COLUNA NOVA ENTRA NO FIM. ``ALTER TABLE ADD COLUMNS`` acrescenta ao
        final do schema da tabela existente; inserir no meio desta tupla faz a
        gravação divergir das tabelas já criadas.
        """
        return (
            self.execution_batch_id,
            self.execution_id,
            int(self.event_seq),
            self.environment,
            self.layer,
            self.domain,
            self.target_table,
            self.source_system,
            self.source_object,
            self.load_strategy,
            self.started_at,
            self.ended_at,
            float(self.duration_seconds) if self.duration_seconds is not None else None,
            float(self.queue_wait_seconds)
            if self.queue_wait_seconds is not None
            else None,
            dt_execution,
            _coerce_date(
                self.window_start
                if self.window_start is not None
                else self.window_start_at
            ),
            _coerce_date(
                self.window_end if self.window_end is not None else self.window_end_at
            ),
            self.status,
            int(self.attempts),
            self.error_type,
            _truncate(self.error_message, MAX_ERROR_CHARS),
            _truncate(self.detail, MAX_DETAIL_CHARS) or "",
            int(self.rows_source or 0),
            int(self.rows_inserted or 0),
            int(self.rows_updated or 0),
            self.job_run_id,
            self.task_run_id,
            self.orchestrator_run_id,
            self.notebook_path,
            self.metrics_json(),
            # ── Colunas acrescentadas depois da primeira versão ──
            _truncate(self.status_detail, MAX_STATUS_DETAIL_CHARS) or "",
            _coerce_datetime(self.window_start_at),
            _coerce_datetime(self.window_end_at),
        )
