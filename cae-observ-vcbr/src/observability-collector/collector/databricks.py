"""Os três passos do ciclo Databricks (documento §3.1).

A Jobs API só filtra por `start_time_from`/`start_time_to` — não existe
filtro por tempo de término. Isso, sozinho, elimina a solução ingênua
("trazer tudo que terminou desde a última marca"): um job iniciado há três
dias está fora de qualquer janela recente de início e nunca apareceria numa
varredura por `start_time`. Daí os três mecanismos, sempre nesta ordem, por
workspace:

  a) `list_active_runs`     — runs/list?active_only=true: o que está aberto
                               agora. Usado para reconciliar tbl_active_executions.
  b) `close_finished_runs`  — runs/get em cada run que estava marcado ativo
                               no ciclo anterior e não veio mais na resposta
                               de (a). runs/get não depende de start_time,
                               então fecha corretamente um job que rodou por
                               dias.
  c) `sweep_completed_runs` — runs/list?completed_only=true, com
                               start_time_from ancorado no menor dos dois:
                               o watermark de sempre, ou o started_at do run
                               aberto mais antigo que sobrou depois de (a).

Sem (a) reconciliar de verdade — apagando o que sumiu, não só inserindo o
que apareceu —, toda execução concluída vira um JOB_STUCK falso assim que o
relógio passa da duração máxima esperada (§4.4): o job nunca sai de
tbl_active_executions porque nada nunca o remove de lá.
"""

from __future__ import annotations

import logging
import os
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import pyodbc
from azure.identity import DefaultAzureCredential

import constants as c
import natural_key as nk
import resolution

logger = logging.getLogger("collector.databricks")


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    workspace_url: str


def connect_sql(credential: DefaultAzureCredential) -> pyodbc.Connection:
    """Conexão por Managed Identity — mesmo padrão de sync-expectations e
    hub-replication. Repetido aqui (em vez de importado) porque os três
    projetos são imagens de contêiner independentes, sem pacote compartilhado
    entre si — ver §13.1 sobre a organização por destino de execução."""
    token = credential.get_token(c.SQL_SCOPE).token
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    SQL_COPT_SS_ACCESS_TOKEN = 1256  # constante do driver ODBC

    connection_string = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{os.environ['SQL_SERVER_FQDN']},1433;"
        f"Database={os.environ['SQL_DATABASE']};Encrypt=yes;"
    )
    return pyodbc.connect(
        connection_string,
        attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct},
    )


def list_workspaces(conn: pyodbc.Connection) -> list[Workspace]:
    rows = conn.cursor().execute(
        "SELECT workspace_id, workspace_url FROM tbl_cfg_workspaces WHERE active = 1"
    )
    return [Workspace(row.workspace_id, row.workspace_url) for row in rows]


# ---------------------------------------------------------------------------
# a) O que está aberto agora
# ---------------------------------------------------------------------------

def _paginate_runs(
    client: httpx.Client, token: str, params: dict[str, object]
) -> list[dict]:
    """Paginador genérico de runs/list — serve para active_only e completed_only."""
    runs: list[dict] = []
    page_token: str | None = None

    while True:
        page_params = dict(params, limit=c.RUNS_LIST_PAGE_SIZE)
        if page_token:
            page_params["page_token"] = page_token

        resp = client.get(
            c.RUNS_LIST_PATH,
            params=page_params,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        payload = resp.json()

        runs.extend(payload.get("runs", []))
        page_token = payload.get("next_page_token")
        if not page_token or not payload.get("has_more", bool(page_token)):
            break

    return runs


def list_active_runs(client: httpx.Client, token: str) -> dict[str, dict]:
    """runs/list?active_only=true, indexado por run_id (string).

    Chave string porque tbl_active_executions.run_id é VARCHAR — a API
    devolve inteiro, e comparar tipos diferentes é a forma mais fácil de
    reconciliação silenciosamente não bater nunca.
    """
    runs = _paginate_runs(client, token, {"active_only": True, "expand_tasks": False})
    return {str(run["run_id"]): run for run in runs if run.get("run_id") is not None}


def get_run(client: httpx.Client, token: str, run_id: str) -> dict:
    """runs/get — única forma determinística de saber se e como uma execução
    concluiu, porque não depende de nenhuma janela de tempo (§3.1b)."""
    resp = client.get(
        c.RUNS_GET_PATH,
        params={"run_id": run_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return resp.json()


def read_active_run_ids(conn: pyodbc.Connection, workspace_id: str) -> set[str]:
    rows = conn.cursor().execute(
        "SELECT run_id FROM tbl_active_executions WHERE workspace_id = ?",
        workspace_id,
    )
    return {row.run_id for row in rows}


_UPSERT_ACTIVE = """
MERGE tbl_active_executions AS target
USING (SELECT ? AS workspace_id, ? AS run_id, ? AS job_id, ? AS run_name,
              ? AS status, ? AS started_at) AS src
   ON target.workspace_id = src.workspace_id AND target.run_id = src.run_id
WHEN MATCHED THEN
  UPDATE SET job_id = src.job_id, run_name = src.run_name, status = src.status,
             started_at = src.started_at, updated_at = SYSDATETIMEOFFSET()
WHEN NOT MATCHED THEN
  INSERT (workspace_id, run_id, job_id, run_name, status, started_at)
  VALUES (src.workspace_id, src.run_id, src.job_id, src.run_name, src.status,
          src.started_at);
"""


def reconcile_active_executions(
    conn: pyodbc.Connection, workspace_id: str, current_runs: dict[str, dict]
) -> set[str]:
    """Reconcilia tbl_active_executions com o que a API diz estar aberto AGORA.

    Devolve os run_ids que SUMIRAM da resposta — candidatos ao passo (b), que
    decide como cada um concluiu. O `WHERE workspace_id = ?` em todo comando
    é o que garante que reconciliar um workspace nunca apaga a linha de
    outro: run_id só é único por workspace (§4.2), então sem esse filtro um
    run_id coincidente entre dois workspaces apagaria a linha errada.
    """
    previous_ids = read_active_run_ids(conn, workspace_id)
    closed_ids = previous_ids - set(current_runs)

    cursor = conn.cursor()
    if closed_ids:
        placeholders = ", ".join("?" for _ in closed_ids)
        cursor.execute(
            f"DELETE FROM tbl_active_executions "
            f"WHERE workspace_id = ? AND run_id IN ({placeholders})",
            workspace_id, *closed_ids,
        )

    for run_id, run in current_runs.items():
        state = run.get("state") or {}
        cursor.execute(
            _UPSERT_ACTIVE,
            workspace_id, run_id, str(run.get("job_id") or "") or None,
            run.get("run_name"), state.get("life_cycle_state", "RUNNING"),
            _epoch_ms_to_datetime(run.get("start_time")),
        )
    conn.commit()

    return closed_ids


def _epoch_ms_to_datetime(epoch_ms: int | None) -> datetime | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)


# ---------------------------------------------------------------------------
# b) Fechar o que ficou em aberto de ciclos anteriores
# ---------------------------------------------------------------------------

def close_finished_runs(
    conn: pyodbc.Connection,
    client: httpx.Client,
    token: str,
    workspace: Workspace,
    closed_run_ids: set[str],
) -> None:
    """Para cada run que sumiu de active_only, pergunta à API como ele
    concluiu e resolve o evento. runs/get, não runs/list: a única chamada
    que não depende de start_time e por isso fecha corretamente um job que
    ficou rodando por dias (§3.1b)."""
    for run_id in closed_run_ids:
        try:
            run = get_run(client, token, run_id)
        except httpx.HTTPStatusError:
            # Run pode ter sido apagado por retenção da própria API entre a
            # reconciliação e agora — raro, mas não pode derrubar o ciclo.
            logger.warning(
                "workspace %s: runs/get falhou para run_id %s",
                workspace.workspace_id, run_id,
            )
            continue

        run.setdefault("workspace_id", workspace.workspace_id)
        _observe_run(conn, run, capture_source=c.CAPTURE_SOURCE_API)


def _floor_started_at(current_runs: dict[str, dict]) -> datetime | None:
    """O menor started_at entre as execuções que continuam abertas agora —
    ancora a janela retroativa do passo (c) na execução aberta mais antiga
    conhecida, em vez de um lookback fixo arbitrário (§3.1b)."""
    started = [
        _epoch_ms_to_datetime(run.get("start_time")) for run in current_runs.values()
    ]
    started = [s for s in started if s is not None]
    return min(started) if started else None


# ---------------------------------------------------------------------------
# c) Varrer os concluídos
# ---------------------------------------------------------------------------

def recovery_start_time(
    watermark: datetime,
    floor_started_at: datetime | None,
    overlap_minutes: int = c.OVERLAP_MINUTES,
) -> datetime:
    """start_time_from do passo (c): min(watermark, piso do passo b) − sobreposição.

    Função pura de propósito — é o cálculo mais fácil de errar em silêncio
    (um `max` no lugar de `min` faz o coletor voltar a perder run longo), e
    o mais barato de testar sem banco nem API.
    """
    anchor = min(watermark, floor_started_at) if floor_started_at else watermark
    return anchor - timedelta(minutes=overlap_minutes)


def sweep_completed_runs(
    client: httpx.Client, token: str, start_time_from: datetime
) -> list[dict]:
    runs = _paginate_runs(
        client, token,
        {
            "completed_only": True,
            "start_time_from": int(start_time_from.timestamp() * 1000),
            "expand_tasks": False,
        },
    )
    return runs


# ---------------------------------------------------------------------------
# Resolução de um run concluído — comum aos passos (b) e (c)
# ---------------------------------------------------------------------------

def _observe_run(
    conn: pyodbc.Connection, run: dict, capture_source: str
) -> None:
    """Registra a observação em tbl_executions e, se o run já concluiu,
    resolve o evento por resolve_event(). Runs ainda em vôo (sem
    result_state terminal) só geram log — quem fecha um run assim é o
    próximo ciclo, via runs/get (passo b)."""
    workspace_id = str(run.get("workspace_id") or "")
    run_id = str(run.get("run_id") or "")
    state = run.get("state") or {}
    life_cycle_state = state.get("life_cycle_state")
    result_state = state.get("result_state")

    identity = nk.resolve(run)
    if identity is None:
        # WORKFLOW_RUN sem pai identificável, ou tipo desconhecido sem
        # run_name: registra para investigação, não alerta (§3.3).
        natural_key = f"{workspace_id}:{run_id}"
        key_rule = "SEM_IDENTIDADE"
    else:
        natural_key = identity.natural_key
        key_rule = identity.rule

    observed_at = _epoch_ms_to_datetime(run.get("start_time")) or datetime.now(timezone.utc)

    is_new = resolution.log_execution(
        conn,
        source=c.SOURCE_DATABRICKS,
        scope=workspace_id,
        natural_key=natural_key,
        run_id=run_id,
        run_name=run.get("run_name"),
        status=life_cycle_state or "UNKNOWN",
        result_state=result_state,
        capture_source=capture_source,
        observed_at=observed_at,
    )
    if not is_new:
        # Mesma execução já vista nesta ou noutra varredura sobreposta
        # (§3.1c usa overlap de propósito). Sem esta guarda, resolve_event()
        # seria chamada duas vezes para o mesmo FAIL — e, sem
        # source_event_id, ela não tem como distinguir isso de uma segunda
        # ocorrência real, dobrando occurrence_count por um evento só.
        return

    if identity is None or life_cycle_state not in c.TERMINAL_LIFE_CYCLE_STATES:
        return
    if result_state == c.RESULT_STATE_CANCELED:
        # Cancelamento deliberado: nem sucesso nem falha do processo (§3.1).
        return

    domain = nk.resolve_domain(identity)
    observation = (
        c.OBSERVATION_PASS if result_state == c.RESULT_STATE_SUCCESS
        else c.OBSERVATION_FAIL
    )
    error_description = None
    if observation == c.OBSERVATION_FAIL:
        error_description = (state.get("state_message") or "")[:1000] or None

    resolution.resolve_event(
        conn,
        source=c.SOURCE_DATABRICKS,
        result=observation,
        natural_key=natural_key,
        key_rule=key_rule,
        # Só registra a execução do ADF que originou este run; a correlação
        # do incidente é a natural_key, estável entre execuções.
        correlation_ref=identity.activity_run_id,
        scope=workspace_id,
        workspace_id=workspace_id,
        severity=c.DEFAULT_SEVERITY,
        error_code=result_state,
        error_description=error_description,
        run_id=run_id,
        observed_at=observed_at,
    )


# ---------------------------------------------------------------------------
# Orquestração de um workspace
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CycleResult:
    active_count: int
    events_observed: int


def run_cycle(
    conn: pyodbc.Connection, credential: DefaultAzureCredential, workspace: Workspace
) -> CycleResult:
    """Um ciclo completo (a, b, c) para um workspace. Erros de um workspace
    não podem impedir os demais — cada um tem watermark e reconciliação
    próprios, e um workspace fora do ar não é motivo para parar de coletar
    dos outros."""
    token = credential.get_token(c.DATABRICKS_SCOPE).token
    watermark = resolution.read_watermark(
        conn, c.watermark_key_databricks(workspace.workspace_id)
    )

    with httpx.Client(base_url=workspace.workspace_url, timeout=60) as client:
        # a) o que está aberto agora
        current_runs = list_active_runs(client, token)
        closed_ids = reconcile_active_executions(
            conn, workspace.workspace_id, current_runs
        )

        # b) fechar o que sumiu desde o ciclo anterior
        close_finished_runs(conn, client, token, workspace, closed_ids)

        # c) varrer concluídos, ancorado no run aberto mais antigo que sobrou
        floor = _floor_started_at(current_runs)
        start_from = recovery_start_time(watermark, floor)
        completed_runs = sweep_completed_runs(client, token, start_from)
        # Runs que o passo (b) já resolveu por runs/get não precisam do
        # segundo tratamento aqui — log_execution() é idempotente e pegaria
        # isso de qualquer forma, mas filtrar evita a chamada redundante.
        completed_runs = [
            r for r in completed_runs if str(r.get("run_id")) not in closed_ids
        ]

        new_watermark = watermark
        for run in completed_runs:
            run.setdefault("workspace_id", workspace.workspace_id)
            _observe_run(conn, run, capture_source=c.CAPTURE_SOURCE_API)
            started_at = _epoch_ms_to_datetime(run.get("start_time"))
            if started_at and started_at > new_watermark:
                new_watermark = started_at

    resolution.advance_watermark(
        conn, c.watermark_key_databricks(workspace.workspace_id), new_watermark
    )
    logger.info(
        "workspace %s: %d ativos, %d fechados, %d concluídos varridos",
        workspace.workspace_id, len(current_runs), len(closed_ids),
        len(completed_runs),
    )
    return CycleResult(
        active_count=len(current_runs),
        events_observed=len(closed_ids) + len(completed_runs),
    )
