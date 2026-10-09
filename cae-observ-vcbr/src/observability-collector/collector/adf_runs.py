"""Sinais de pipeline run e trigger run do ADF (Log Analytics).

Complementa `adf.py`, que só lê `ADFActivityRun`. Dois buracos que a atividade
sozinha não cobre:

* **Pipeline que falha sem executar atividade nenhuma** (erro de parâmetro,
  de configuração, de permissão): não existe linha em `ADFActivityRun`, então
  nenhum incidente era aberto.
* **Trigger que falha ao disparar**: o pipeline nem chega a existir.

Regras:

* A chave do incidente é **estável** (`<factory>/<pipeline>/pipeline` e
  `<factory>/trigger/<nome>`), não por execução. É isso que faz o `PASS` da
  execução seguinte fechar o incidente — o que o incidente de atividade
  (`adf:<activity_run_id>`) não consegue, ver D08 em doc/02.
* Pipeline `Failed` **só abre incidente quando nenhuma atividade da mesma
  execução falhou**. Se alguma falhou, o incidente de atividade já cobre a
  causa e um segundo alerta seria duplicado.
* Por isso a carência (`PIPELINE_FAILURE_GRACE_MINUTES`): a linha do pipeline
  pode chegar ao Log Analytics antes das linhas de atividade da mesma
  execução. Pipeline falho só é avaliado depois de a carência passar.
* `Cancelled` é só registrado em `tbl_executions`, sem incidente (mesma regra
  do Databricks).
* Os watermarks avançam sobre `ingestion_time()`, não sobre `TimeGenerated`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import pyodbc
from azure.identity import DefaultAzureCredential

import constants as c
import resolution
from adf import _parse_kql_datetime, query_log_analytics, rows_as_dicts

logger = logging.getLogger("collector.adf_runs")

#: Extrai o nome do Data Factory do ResourceId (nenhuma das tabelas tem coluna
#: própria para isso).
_FACTORY_FROM_RESOURCE_ID = (
    'tostring(extract(@"(?i)/FACTORIES/([^/]+)", 1, _ResourceId))'
)

_RUN_ID_PATTERN = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def kql_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def event_id(*parts: str) -> str:
    """`source_event_id` determinístico: o mesmo sinal relido na sobreposição
    da janela vira `DUPLICADO` em `resolve_event`, sem efeito."""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Pipeline run
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PipelineRunEvent:
    run_id: str
    data_factory: str
    pipeline_name: str
    status: str  # Succeeded | Failed | Cancelled
    error: str | None
    ended_at: datetime
    ingested_at: datetime


_PIPELINE_QUERY = """
{table}
| extend IngestedAt = ingestion_time()
| where IngestedAt > datetime({since})
| where Status in ("{succeeded}", "{failed}", "{cancelled}")
| extend DataFactoryName = {factory}
| summarize arg_max(TimeGenerated, *) by RunId
| project TimeGenerated, IngestedAt, RunId, DataFactoryName, PipelineName,
          Status, End, ErrorCode, ErrorMessage
"""

_FAILED_ACTIVITY_QUERY = """
{table}
| where TimeGenerated > datetime({since})
| where Status == "{failed}"
| where PipelineRunId in ({ids})
| distinct PipelineRunId
"""


def _error_text(row: dict) -> str | None:
    message = (row.get("ErrorMessage") or "").strip()
    code = (row.get("ErrorCode") or "").strip()
    if message and code:
        return f"{code}: {message}"
    return message or code or None


def query_pipeline_runs(
    credential: DefaultAzureCredential, workspace_id: str, since: datetime
) -> list[PipelineRunEvent]:
    kql = _PIPELINE_QUERY.format(
        table=c.TABLE_ADF_PIPELINE_RUN,
        since=kql_datetime(since),
        succeeded=c.ADF_STATUS_SUCCEEDED,
        failed=c.ADF_STATUS_FAILED,
        cancelled=c.ADF_STATUS_CANCELLED,
        factory=_FACTORY_FROM_RESOURCE_ID,
    )
    events = []
    for row in rows_as_dicts(query_log_analytics(credential, workspace_id, kql)):
        if not row.get("RunId"):
            continue
        generated = _parse_kql_datetime(row["TimeGenerated"])
        events.append(PipelineRunEvent(
            run_id=str(row["RunId"]).lower(),
            data_factory=str(row.get("DataFactoryName") or ""),
            pipeline_name=str(row.get("PipelineName") or ""),
            status=row["Status"],
            error=_error_text(row),
            ended_at=_parse_kql_datetime(row["End"]) if row.get("End") else generated,
            ingested_at=_parse_kql_datetime(row["IngestedAt"]),
        ))
    return events


def query_failed_activity_pipeline_runs(
    credential: DefaultAzureCredential,
    workspace_id: str,
    pipeline_run_ids: list[str],
    since: datetime,
) -> set[str]:
    """IDs, entre os informados, de execuções de pipeline que tiveram ao menos
    uma atividade `Failed`. Os IDs entram na consulta como texto: só passam os
    que têm formato de GUID."""
    valid = [i for i in pipeline_run_ids if _RUN_ID_PATTERN.match(i)]
    if not valid:
        return set()
    kql = _FAILED_ACTIVITY_QUERY.format(
        table=c.TABLE_ADF_ACTIVITY_RUN,
        since=kql_datetime(since),
        failed=c.ADF_STATUS_FAILED,
        ids=", ".join(f'"{i}"' for i in valid),
    )
    rows = rows_as_dicts(query_log_analytics(credential, workspace_id, kql))
    return {str(r["PipelineRunId"]).lower() for r in rows if r.get("PipelineRunId")}


def pipeline_natural_key(event: PipelineRunEvent) -> str:
    return f"{event.data_factory}/{event.pipeline_name}/pipeline".lower()


def split_deferred(
    events: list[PipelineRunEvent], cutoff: datetime
) -> tuple[list[PipelineRunEvent], list[PipelineRunEvent]]:
    """Separa o que pode ser avaliado agora do que precisa esperar a carência.

    Só pipeline `Failed` espera. Quando um `Failed` fica de fora, toda
    execução posterior do MESMO pipeline também fica: avaliar um `Succeeded`
    mais novo antes do `Failed` mais velho deixaria o incidente aberto depois
    de a situação já ter voltado ao normal."""
    ready: list[PipelineRunEvent] = []
    deferred: list[PipelineRunEvent] = []
    blocked: set[str] = set()
    for event in sorted(events, key=lambda e: e.ended_at):
        key = pipeline_natural_key(event)
        waits = event.status == c.ADF_STATUS_FAILED and event.ended_at > cutoff
        if waits or key in blocked:
            blocked.add(key)
            deferred.append(event)
        else:
            ready.append(event)
    return ready, deferred


def next_watermark(
    current: datetime,
    ready: list[PipelineRunEvent],
    deferred: list[PipelineRunEvent],
) -> datetime:
    """Avança até o que foi processado, sem passar do que ficou esperando: a
    linha adiada precisa continuar dentro da janela do próximo ciclo."""
    candidate = max((e.ingested_at for e in ready), default=current)
    if deferred:
        candidate = min(
            candidate,
            min(e.ingested_at for e in deferred) - timedelta(microseconds=1),
        )
    return max(current, candidate)


def run_pipeline_cycle(
    conn: pyodbc.Connection,
    credential: DefaultAzureCredential,
    log_analytics_workspace_id: str,
    now: datetime | None = None,
) -> int:
    """Devolve quantos eventos de pipeline foram avaliados (não adiados)."""
    now = now or datetime.now(timezone.utc)
    watermark = resolution.read_watermark(conn, c.WATERMARK_KEY_ADF_PIPELINE)
    since = watermark - timedelta(minutes=c.OVERLAP_MINUTES)

    try:
        events = query_pipeline_runs(credential, log_analytics_workspace_id, since)
    except httpx.HTTPStatusError:
        logger.exception("falha ao consultar ADFPipelineRun no Log Analytics")
        return 0

    cutoff = now - timedelta(minutes=c.PIPELINE_FAILURE_GRACE_MINUTES)
    ready, deferred = split_deferred(events, cutoff)
    if deferred:
        logger.info(
            "ADF pipeline: %d execução(ões) falha(s) aguardam a carência de "
            "%d min antes de serem avaliadas",
            len(deferred), c.PIPELINE_FAILURE_GRACE_MINUTES,
        )

    failed_ids = [e.run_id for e in ready if e.status == c.ADF_STATUS_FAILED]
    explained: set[str] = set()
    if failed_ids:
        # Sem esta resposta não dá para decidir; a exceção interrompe o ciclo
        # antes de gravar qualquer coisa, e o próximo reavalia tudo.
        explained = query_failed_activity_pipeline_runs(
            credential, log_analytics_workspace_id, failed_ids,
            min(e.ended_at for e in ready) - timedelta(hours=1),
        )

    for event in ready:
        natural_key = pipeline_natural_key(event)
        is_new = resolution.log_execution(
            conn,
            source=c.SOURCE_ADF,
            scope=event.data_factory,
            natural_key=natural_key,
            run_id=event.run_id,
            run_name=event.pipeline_name,
            status=event.status,
            result_state=event.status,
            capture_source=c.CAPTURE_SOURCE_LOG_ANALYTICS,
            observed_at=event.ended_at,
        )
        if not is_new or event.status == c.ADF_STATUS_CANCELLED:
            continue

        if event.status == c.ADF_STATUS_SUCCEEDED:
            observation, description, error_code = c.OBSERVATION_PASS, None, None
        elif event.run_id in explained:
            # Uma atividade desta execução falhou: o incidente de atividade
            # já explica o pipeline.
            continue
        else:
            observation = c.OBSERVATION_FAIL
            error_code = c.ERROR_CODE_ADF_PIPELINE_FAILED
            description = (
                f"Pipeline '{event.pipeline_name}' (ADF {event.data_factory}) "
                f"falhou sem que nenhuma atividade falhasse"
                + (f": {event.error}" if event.error else ".")
            )[:1000]

        resolution.resolve_event(
            conn,
            source=c.SOURCE_ADF,
            result=observation,
            natural_key=natural_key,
            key_rule=c.KEY_RULE_ADF_PIPELINE,
            scope=event.data_factory,
            severity=c.DEFAULT_SEVERITY,
            error_code=error_code,
            error_description=description,
            run_id=event.run_id,
            source_event_id=event_id("adf-pipeline", event.run_id),
            observed_at=event.ended_at,
        )

    resolution.advance_watermark(
        conn, c.WATERMARK_KEY_ADF_PIPELINE, next_watermark(watermark, ready, deferred)
    )
    logger.info("ADF pipeline: %d execuções avaliadas", len(ready))
    return len(ready)


# ---------------------------------------------------------------------------
# Trigger run
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TriggerRunEvent:
    data_factory: str
    trigger_name: str
    status: str  # Succeeded | Failed
    error: str | None
    started_at: datetime
    ingested_at: datetime


_TRIGGER_QUERY = """
{table}
| extend IngestedAt = ingestion_time()
| where IngestedAt > datetime({since})
| where Status in ("{succeeded}", "{failed}")
| extend DataFactoryName = {factory}
| summarize arg_max(TimeGenerated, *) by DataFactoryName, TriggerName, Start
| project TimeGenerated, IngestedAt, DataFactoryName, TriggerName, Status,
          Start, ErrorCode, ErrorMessage, TriggerFailureType
"""


def query_trigger_runs(
    credential: DefaultAzureCredential, workspace_id: str, since: datetime
) -> list[TriggerRunEvent]:
    kql = _TRIGGER_QUERY.format(
        table=c.TABLE_ADF_TRIGGER_RUN,
        since=kql_datetime(since),
        succeeded=c.ADF_STATUS_SUCCEEDED,
        failed=c.ADF_STATUS_FAILED,
        factory=_FACTORY_FROM_RESOURCE_ID,
    )
    events = []
    for row in rows_as_dicts(query_log_analytics(credential, workspace_id, kql)):
        if not row.get("TriggerName"):
            continue
        generated = _parse_kql_datetime(row["TimeGenerated"])
        failure_type = (row.get("TriggerFailureType") or "").strip()
        text = _error_text(row)
        events.append(TriggerRunEvent(
            data_factory=str(row.get("DataFactoryName") or ""),
            trigger_name=str(row["TriggerName"]),
            status=row["Status"],
            error=" — ".join(p for p in (failure_type, text) if p) or None,
            started_at=_parse_kql_datetime(row["Start"]) if row.get("Start") else generated,
            ingested_at=_parse_kql_datetime(row["IngestedAt"]),
        ))
    return events


def trigger_natural_key(event: TriggerRunEvent) -> str:
    return f"{event.data_factory}/trigger/{event.trigger_name}".lower()


def run_trigger_cycle(
    conn: pyodbc.Connection,
    credential: DefaultAzureCredential,
    log_analytics_workspace_id: str,
) -> int:
    """Devolve quantos disparos de trigger foram avaliados."""
    watermark = resolution.read_watermark(conn, c.WATERMARK_KEY_ADF_TRIGGER)
    since = watermark - timedelta(minutes=c.OVERLAP_MINUTES)

    try:
        events = query_trigger_runs(credential, log_analytics_workspace_id, since)
    except httpx.HTTPStatusError:
        logger.exception("falha ao consultar ADFTriggerRun no Log Analytics")
        return 0

    new_watermark = watermark
    for event in sorted(events, key=lambda e: e.started_at):
        natural_key = trigger_natural_key(event)
        failed = event.status == c.ADF_STATUS_FAILED
        description = None
        if failed:
            description = (
                f"Trigger '{event.trigger_name}' (ADF {event.data_factory}) "
                f"falhou ao disparar"
                + (f": {event.error}" if event.error else ".")
            )[:1000]

        resolution.resolve_event(
            conn,
            source=c.SOURCE_ADF,
            result=c.OBSERVATION_FAIL if failed else c.OBSERVATION_PASS,
            natural_key=natural_key,
            key_rule=c.KEY_RULE_ADF_TRIGGER,
            scope=event.data_factory,
            severity=c.DEFAULT_SEVERITY,
            error_code=c.ERROR_CODE_ADF_TRIGGER_FAILED if failed else None,
            error_description=description,
            source_event_id=event_id(
                "adf-trigger", natural_key, event.started_at.isoformat(), event.status
            ),
            observed_at=event.started_at,
        )
        new_watermark = max(new_watermark, event.ingested_at)

    resolution.advance_watermark(conn, c.WATERMARK_KEY_ADF_TRIGGER, new_watermark)
    logger.info("ADF trigger: %d disparos avaliados", len(events))
    return len(events)
