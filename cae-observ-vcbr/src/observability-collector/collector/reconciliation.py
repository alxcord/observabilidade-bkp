"""Rede de apoio: compara o capturado pela Jobs API com a tabela
`DatabricksJobs` do Log Analytics (documento §3.1c).

**Nunca abre incidente sozinho antes de o próximo ciclo confirmar pela
API** — este sinal carrega a mesma incerteza de latência que motivou tirar
o alerta das System Tables (§1.1). O papel dele é só preencher
`tbl_executions` com `capture_source = 'LOG_ANALYTICS'` quando um run passa
por aqui e nunca chega pela Jobs API, e contar como gap preenchido.

**Por que existe uma tabela de candidatos.** O texto do documento pede
"ausente **por mais de um ciclo**" antes de agir — e este job não guarda
nada em memória entre execuções, porque recomeça do zero a cada disparo do
Container Apps Job (§13.2). `tbl_ctl_reconciliation_gaps` é essa memória:
um run só vira linha em `tbl_executions` na SEGUNDA vez que aparece ausente
das tabelas locais, nunca na primeira — a primeira só grava o candidato.
Isso evita contar como "gap" um run que a Jobs API só ainda não processou
neste ciclo específico, por pura diferença de timing entre as duas fontes.

  NOTA — schema de `DatabricksJobs` não verificado em dado real (Apêndice C
  do inventário de arquitetura trata isso como pendência de medição, item
  8). A consulta assume o schema documentado publicamente para audit logs
  do Databricks via Diagnostic Settings: `ActionName` identificando o tipo
  de evento, `RequestParams` como JSON com `runId`/`jobId`. Validar contra
  ~20 execuções reais antes de depender disto em produção.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pyodbc
from azure.identity import DefaultAzureCredential

import adf
import constants as c
import resolution

logger = logging.getLogger("collector.reconciliation")


_QUERY = """
{table}
| where TimeGenerated > datetime({since})
| where ActionName in ("runSucceeded", "runFailed", "runTriggered")
| extend RunId = tostring(RequestParams["runId"]), JobId = tostring(RequestParams["jobId"])
| where isnotempty(RunId)
| summarize arg_max(TimeGenerated, *) by WorkspaceId, RunId
| project TimeGenerated, WorkspaceId, RunId, JobId
"""


def _fetch_log_analytics_runs(
    credential: DefaultAzureCredential, log_analytics_workspace_id: str, since: datetime
) -> dict[tuple[str, str], str | None]:
    """Devolve {(workspace_id, run_id): job_id} — job_id só entra para
    compor um natural_key mais legível ao promover um gap; a chave do dict
    é o que `find_gaps` de fato compara."""
    kql = _QUERY.format(
        table=c.TABLE_DATABRICKS_JOBS,
        since=since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    )
    payload = adf.query_log_analytics(credential, log_analytics_workspace_id, kql)
    return {
        (str(row["WorkspaceId"]), str(row["RunId"])): (
            str(row["JobId"]) if row.get("JobId") else None
        )
        for row in adf.rows_as_dicts(payload)
        if row.get("WorkspaceId") and row.get("RunId")
    }


def _fetch_known_runs(conn: pyodbc.Connection, since: datetime) -> set[tuple[str, str]]:
    """O que já é conhecido localmente — de qualquer fonte de captura.

    Inclui `tbl_active_executions` inteira (tabela pequena, sempre o
    presente) e `tbl_executions` só desde `since`, para não escanear o log
    append-only inteiro a cada ciclo.
    """
    cursor = conn.cursor()
    known: set[tuple[str, str]] = set()
    for row in cursor.execute("SELECT workspace_id, run_id FROM tbl_active_executions"):
        known.add((row.workspace_id, row.run_id))
    for row in cursor.execute(
        "SELECT scope, run_id FROM tbl_executions "
        "WHERE source = ? AND observed_at > ?",
        c.SOURCE_DATABRICKS, since,
    ):
        known.add((row.scope, row.run_id))
    return known


def find_gaps(
    log_analytics_runs: set[tuple[str, str]], known_runs: set[tuple[str, str]]
) -> set[tuple[str, str]]:
    """Presente no Log Analytics, ausente localmente. Função pura — é a
    parte desta rede de apoio que não depende de schema incerto nem de rede,
    e por isso a mais barata e mais importante de testar sem os dois."""
    return log_analytics_runs - known_runs


# ---------------------------------------------------------------------------
# tbl_ctl_reconciliation_gaps — memória de candidatos entre ciclos
# ---------------------------------------------------------------------------

def _read_candidates(conn: pyodbc.Connection) -> set[tuple[str, str]]:
    rows = conn.cursor().execute(
        "SELECT workspace_id, run_id FROM tbl_ctl_reconciliation_gaps"
    )
    return {(row.workspace_id, row.run_id) for row in rows}


def _record_candidate(conn: pyodbc.Connection, workspace_id: str, run_id: str) -> None:
    conn.cursor().execute(
        "IF NOT EXISTS (SELECT 1 FROM tbl_ctl_reconciliation_gaps "
        "                WHERE workspace_id = ? AND run_id = ?) "
        "INSERT INTO tbl_ctl_reconciliation_gaps (workspace_id, run_id) VALUES (?, ?)",
        workspace_id, run_id, workspace_id, run_id,
    )


def _clear_candidate(conn: pyodbc.Connection, workspace_id: str, run_id: str) -> None:
    conn.cursor().execute(
        "DELETE FROM tbl_ctl_reconciliation_gaps WHERE workspace_id = ? AND run_id = ?",
        workspace_id, run_id,
    )


def _promote_gap(
    conn: pyodbc.Connection, workspace_id: str, run_id: str, job_id: str | None,
    observed_at: datetime,
) -> None:
    """Ausente por dois ciclos seguidos: registra em tbl_executions como
    gap preenchido pela rede de apoio. Nunca chama resolve_event() — este
    sinal não decide incidente sozinho (ver nota no topo do módulo)."""
    natural_key = f"{workspace_id}:{job_id}" if job_id else f"{workspace_id}:{run_id}"
    resolution.log_execution(
        conn,
        source=c.SOURCE_DATABRICKS,
        scope=workspace_id,
        natural_key=natural_key,
        run_id=run_id,
        run_name=None,
        status="UNKNOWN",
        result_state=None,
        capture_source=c.CAPTURE_SOURCE_LOG_ANALYTICS,
        observed_at=observed_at,
    )
    logger.warning(
        "gap preenchido pela rede de apoio: workspace=%s run_id=%s "
        "(visto no Log Analytics, ausente da Jobs API por mais de um ciclo)",
        workspace_id, run_id,
    )
    _clear_candidate(conn, workspace_id, run_id)


def run_cycle(
    conn: pyodbc.Connection,
    credential: DefaultAzureCredential,
    log_analytics_workspace_id: str,
) -> int:
    """Devolve quantos gaps foram promovidos neste ciclo — o heartbeat (§12)
    reporta esta contagem."""
    watermark = resolution.read_watermark(conn, c.WATERMARK_KEY_DATABRICKS_RECONCILE)
    since = watermark - timedelta(minutes=c.OVERLAP_MINUTES)

    try:
        job_ids_by_run = _fetch_log_analytics_runs(
            credential, log_analytics_workspace_id, since
        )
    except Exception:
        logger.exception("falha ao consultar a rede de apoio (DatabricksJobs)")
        return 0

    known_runs = _fetch_known_runs(conn, since)
    gaps = find_gaps(set(job_ids_by_run), known_runs)
    candidates = _read_candidates(conn)

    promoted = 0
    now = datetime.now(timezone.utc)

    for key in gaps:
        workspace_id, run_id = key
        if key in candidates:
            _promote_gap(conn, workspace_id, run_id, job_ids_by_run.get(key), now)
            promoted += 1
        else:
            _record_candidate(conn, workspace_id, run_id)

    # Candidatos que deixaram de ser gap (a Jobs API alcançou entretanto):
    # limpa, para não promover um falso gap se ele sumir de novo por outro
    # motivo no futuro.
    resolved_candidates = candidates - gaps
    for workspace_id, run_id in resolved_candidates:
        _clear_candidate(conn, workspace_id, run_id)

    conn.commit()
    resolution.advance_watermark(conn, c.WATERMARK_KEY_DATABRICKS_RECONCILE, now)
    logger.info(
        "rede de apoio: %d runs no Log Analytics, %d gaps, %d promovidos",
        len(log_analytics_runs), len(gaps), promoted,
    )
    return promoted
