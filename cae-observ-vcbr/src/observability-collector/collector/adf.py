"""Consulta ao Log Analytics para o sinal primário do ADF (documento §3.2).

Fonte: Diagnostic Settings do ADF sobre `PipelineRuns`/`ActivityRuns`/
`TriggerRuns`, ingeridos como tabelas "resource specific" no Log Analytics.
Este módulo lê `ADFActivityRun` — é ali que mora o `ActivityRunId` que casa
com o sufixo do `run_name` de um `SUBMIT_RUN` disparado pelo ADF (§3.4), e é
por *activity*, não por *pipeline*, que a correlação com o Databricks
funciona: um pipeline pode ter atividades que não são notebook nenhum, e é
a atividade específica, não o pipeline inteiro, que carrega o
`ActivityRunId`.

**Watermark sobre `TimeGenerated`** — o instante de ingestão no workspace,
nunca o tempo do evento (`Start`/`End` da atividade). `TimeGenerated` é
monotônico; tempo de evento não é, porque a latência de ingestão do Log
Analytics varia — um watermark sobre tempo de evento perderia para sempre
qualquer linha que chegasse atrasada.

Também é a origem que consulta `reconciliation.py`, reaproveitando
`query_log_analytics` para a tabela `DatabricksJobs` (rede de apoio, §3.1c).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import pyodbc
from azure.identity import DefaultAzureCredential

import constants as c
import resolution

logger = logging.getLogger("collector.adf")


@dataclass(frozen=True)
class AdfActivityEvent:
    activity_run_id: str
    pipeline_run_id: str
    data_factory: str
    pipeline_name: str
    activity_name: str
    status: str  # Succeeded | Failed
    error: str | None
    time_generated: datetime


def _parse_kql_datetime(value: str) -> datetime:
    """A Logs Query API devolve datetime como string ISO 8601 em UTC, com
    até 7 dígitos de fração (precisão de 100ns) — um a mais do que
    `datetime.fromisoformat` aceita. Trunca para microssegundos, que é toda
    a precisão que o Python guarda mesmo."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    head, _, tz = value.partition("+")
    if "." in head:
        integer, frac = head.split(".", 1)
        head = f"{integer}.{frac[:6]}"
    return datetime.fromisoformat(f"{head}+{tz}" if tz else head)


def query_log_analytics(
    credential: DefaultAzureCredential, workspace_id: str, kql: str, timeout: float = 60
) -> dict:
    """Executor genérico de KQL pela Logs Query API — usado tanto para o
    sinal ADF (este módulo) quanto para a rede de apoio do Databricks
    (`reconciliation.py`, tabela `DatabricksJobs`)."""
    token = credential.get_token(c.LOG_ANALYTICS_SCOPE).token
    url = c.LOG_ANALYTICS_QUERY_URL.format(workspace_id=workspace_id)
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            url, json={"query": kql}, headers={"Authorization": f"Bearer {token}"}
        )
        resp.raise_for_status()
        return resp.json()


def rows_as_dicts(payload: dict) -> list[dict]:
    """Formato de resposta da Logs Query API: `tables[0].columns/rows`."""
    tables = payload.get("tables") or []
    if not tables:
        return []
    columns = [col["name"] for col in tables[0].get("columns", [])]
    return [dict(zip(columns, row)) for row in tables[0].get("rows", [])]


# `summarize arg_max(TimeGenerated, *) by ActivityRunId` é o dedup exigido
# pelo documento: Diagnostic Settings pode reingerir a mesma linha, e
# processar as duas contaria a mesma falha (ou o mesmo sucesso) duas vezes.
_ACTIVITY_QUERY = """
{table}
| where TimeGenerated > datetime({since})
| where Status in ("{succeeded}", "{failed}")
| summarize arg_max(TimeGenerated, *) by ActivityRunId
| project TimeGenerated, ActivityRunId, PipelineRunId, DataFactoryName,
          PipelineName, ActivityName, Status, Error
"""


def query_activity_runs(
    credential: DefaultAzureCredential, workspace_id: str, since: datetime
) -> list[AdfActivityEvent]:
    kql = _ACTIVITY_QUERY.format(
        table=c.TABLE_ADF_ACTIVITY_RUN,
        since=since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        succeeded=c.ADF_STATUS_SUCCEEDED,
        failed=c.ADF_STATUS_FAILED,
    )
    payload = query_log_analytics(credential, workspace_id, kql)

    events = []
    for row in rows_as_dicts(payload):
        if not row.get("ActivityRunId"):
            continue
        events.append(AdfActivityEvent(
            activity_run_id=str(row["ActivityRunId"]).lower(),
            pipeline_run_id=str(row.get("PipelineRunId") or ""),
            data_factory=str(row.get("DataFactoryName") or ""),
            pipeline_name=str(row.get("PipelineName") or ""),
            activity_name=str(row.get("ActivityName") or ""),
            status=row["Status"],
            error=(row.get("Error") or None),
            time_generated=_parse_kql_datetime(row["TimeGenerated"]),
        ))
    return events


def run_cycle(
    conn: pyodbc.Connection,
    credential: DefaultAzureCredential,
    log_analytics_workspace_id: str,
) -> int:
    """Um ciclo do sinal ADF: watermark único, porque a consulta cobre todos
    os Data Factories de uma vez — não há um workspace por ADF como há para
    Databricks. Devolve quantos activity runs foram processados."""
    watermark = resolution.read_watermark(conn, c.WATERMARK_KEY_ADF)
    since = watermark - timedelta(minutes=c.OVERLAP_MINUTES)

    try:
        events = query_activity_runs(credential, log_analytics_workspace_id, since)
    except httpx.HTTPStatusError:
        logger.exception("falha ao consultar Log Analytics para o sinal ADF")
        return 0

    new_watermark = watermark
    for event in events:
        # Estável entre execuções da mesma atividade — o mesmo papel que
        # `identity.natural_key` cumpre para o Databricks em natural_key.py —,
        # e desde a correção do fechamento automático é também a chave de
        # correlação do incidente.
        natural_key = (
            f"{event.data_factory}/{event.pipeline_name}/{event.activity_name}"
        ).lower()

        is_new = resolution.log_execution(
            conn,
            source=c.SOURCE_ADF,
            scope=event.data_factory,
            natural_key=natural_key,
            run_id=event.activity_run_id,
            run_name=f"{event.pipeline_name}/{event.activity_name}",
            status=event.status,
            result_state=event.status,
            capture_source=c.CAPTURE_SOURCE_API,
            observed_at=event.time_generated,
        )

        if is_new:
            observation = (
                c.OBSERVATION_PASS if event.status == c.ADF_STATUS_SUCCEEDED
                else c.OBSERVATION_FAIL
            )
            resolution.resolve_event(
                conn,
                source=c.SOURCE_ADF,
                result=observation,
                natural_key=natural_key,
                key_rule=c.KEY_RULE_ADF_ACTIVITY,
                # O ActivityRunId NÃO entra na correlação: ele é novo a cada
                # execução, e o incidente precisa de uma chave estável para o
                # sucesso seguinte poder fechá-lo. Vai para `correlation_ref`,
                # que liga este evento ao run Databricks que a mesma atividade
                # disparou (ver doc/02).
                correlation_ref=event.activity_run_id,
                scope=event.data_factory,
                severity=c.DEFAULT_SEVERITY,
                error_code=None if observation == c.OBSERVATION_PASS
                else c.ERROR_CODE_ADF_ACTIVITY_FAILED,
                error_description=(event.error or "")[:1000] or None,
                run_id=event.pipeline_run_id,
                observed_at=event.time_generated,
            )

        if event.time_generated > new_watermark:
            new_watermark = event.time_generated

    resolution.advance_watermark(conn, c.WATERMARK_KEY_ADF, new_watermark)
    logger.info("ADF: %d activity runs processados", len(events))
    return len(events)
