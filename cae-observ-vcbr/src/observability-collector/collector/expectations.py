"""Avaliador de ausência de execução e job travado (documento §4.4).

O par simétrico: "não apareceu quando devia começar" e "começou e não
terminou quando devia". Os dois lêem `tbl_cfg_execution_expectations` —
gravada já normalizada por `sync_execution_expectations.py` — e nunca
interpretam cron: a complexidade de traduzir Quartz (Databricks) ou a
recorrência do ADF para `schedule_kind` fica inteiramente na sincronização.
Aqui só há aritmética de data sobre colunas.

Roda no mesmo ciclo do coletor (`__main__.py`), depois do sinal
Databricks/ADF: precisa que `resolution.log_execution` já tenha gravado as
observações do ciclo corrente antes de decidir o que está ausente, senão
uma execução que chegou atrasada mas dentro da tolerância pareceria ausente
por uma corrida de ordem dentro do próprio ciclo.

**Correlação com a execução real.** `resolve_event` calcula a mesma
`correlation_key` (`<source>:<natural_key>`, minúsculo) que a observação
direta de Databricks/ADF usaria para esse job — de propósito: quando a
execução finalmente aparece (ou o job destravar), o ciclo normal chama
`resolve_event(result='PASS'|'FAIL', ...)` para a mesma chave e fecha o
incidente que este módulo abriu. Nenhuma lógica de fechamento mora aqui.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pyodbc

import constants as c
import holidays
import resolution

logger = logging.getLogger("collector.expectations")

#: Ordem de datetime.date.weekday() (0=segunda) — mesmo vocabulário gravado
#: em tbl_cfg_execution_expectations.days_of_week (ver sync_execution_
#: expectations.py, _QUARTZ_WEEKDAYS / _ADF_WEEKDAY_TO_QUARTZ).
_WEEKDAY_CODES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")

#: Quantos dias olhar pra trás procurando a última ocorrência esperada.
#: Cobre até o pior caso mensal (feriado emendando com fim de mês) com
#: margem larga; o custo é irrelevante (é só aritmética de data, sem I/O,
#: e a lista de expectativas ativas é pequena).
_LOOKBACK_DAYS = 400


@dataclass(frozen=True)
class Expectation:
    """Espelha as colunas de tbl_cfg_execution_expectations relevantes para
    o avaliador — nunca a linha inteira (config_source, written_by e
    source_expression são assunto da sincronização, não da avaliação)."""

    source: str
    natural_key: str
    schedule_kind: str  # 'INTERVAL' | 'TIME_OF_DAY' | 'MONTHLY'
    interval_unit: str | None
    interval_value: int | None
    time_of_day: object | None  # datetime.time, como o driver ODBC devolve
    days_of_week: str | None
    day_of_month: int | None
    last_day_of_month: bool
    observes_national_holidays: bool
    timezone: str
    tolerance_minutes: int


_LIST_ACTIVE = """
SELECT source, natural_key, schedule_kind, interval_unit, interval_value,
       time_of_day, days_of_week, day_of_month, last_day_of_month,
       observes_national_holidays, timezone, tolerance_minutes
  FROM tbl_cfg_execution_expectations
 WHERE active = 1;
"""


def list_active(conn: pyodbc.Connection) -> list[Expectation]:
    return [
        Expectation(
            source=row.source, natural_key=row.natural_key,
            schedule_kind=row.schedule_kind, interval_unit=row.interval_unit,
            interval_value=row.interval_value, time_of_day=row.time_of_day,
            days_of_week=row.days_of_week, day_of_month=row.day_of_month,
            last_day_of_month=bool(row.last_day_of_month),
            observes_national_holidays=bool(row.observes_national_holidays),
            timezone=row.timezone, tolerance_minutes=row.tolerance_minutes,
        )
        for row in conn.cursor().execute(_LIST_ACTIVE)
    ]


# ---------------------------------------------------------------------------
# Ausência de execução
# ---------------------------------------------------------------------------

def _occurs_on(exp: Expectation, d: date) -> bool:
    """A expectativa tinha ocorrência prevista no dia `d`?"""
    if exp.schedule_kind == "TIME_OF_DAY":
        if exp.days_of_week:
            allowed = exp.days_of_week.split(",")
            if _WEEKDAY_CODES[d.weekday()] not in allowed:
                return False
    elif exp.schedule_kind == "MONTHLY":
        if exp.last_day_of_month:
            if d.day != calendar.monthrange(d.year, d.month)[1]:
                return False
        elif d.day != exp.day_of_month:
            return False
    else:
        return False

    if not exp.observes_national_holidays and holidays.is_national_holiday(d):
        return False
    return True


def _last_due(exp: Expectation, now_local: datetime) -> datetime | None:
    """Última ocorrência esperada em `exp.time_of_day`, no fuso de
    `now_local`, no dia `d` mais recente (incluindo hoje) tal que a
    ocorrência já tenha passado. None se nenhum dia nos últimos
    `_LOOKBACK_DAYS` bater — expectativa mal configurada (ex.: day_of_month
    inválido), registrada em log, nunca em alerta de ausência."""
    for offset in range(_LOOKBACK_DAYS):
        d = (now_local - timedelta(days=offset)).date()
        if not _occurs_on(exp, d):
            continue
        candidate = datetime.combine(d, exp.time_of_day, tzinfo=now_local.tzinfo)
        if candidate <= now_local:
            return candidate
    return None


_LAST_OBSERVED = """
SELECT MAX(observed_at) AS last_observed
  FROM tbl_executions
 WHERE source = ?
   -- ADF agenda por pipeline (natural_key da expectativa), mas cada
   -- execução observada é por atividade dentro do pipeline (natural_key =
   -- '<data_factory>/<pipeline>/<atividade>', ver adf.py) — o LIKE cobre
   -- essa granularidade mais fina sem afetar Databricks, cujo natural_key
   -- nunca tem barra.
   AND (natural_key = ? OR natural_key LIKE ? + '/%');
"""


def _last_observed(
    conn: pyodbc.Connection, source: str, natural_key: str
) -> datetime | None:
    row = conn.cursor().execute(
        _LAST_OBSERVED, source, natural_key, natural_key
    ).fetchone()
    return row.last_observed if row and row.last_observed else None


def _interval_timedelta(exp: Expectation) -> timedelta:
    if exp.interval_unit == "HOUR":
        return timedelta(hours=exp.interval_value)
    return timedelta(minutes=exp.interval_value)


def evaluate_absence(
    conn: pyodbc.Connection,
    expectations: list[Expectation],
    now: datetime | None = None,
) -> int:
    """Abre `JOB_NOT_EXECUTED` para toda expectativa ativa cuja ocorrência
    esperada mais a tolerância já passou sem execução observada desde
    então. Devolve quantos incidentes foram abertos ou incrementados."""
    now = now or datetime.now(timezone.utc)
    opened = 0

    for exp in expectations:
        try:
            tz = ZoneInfo(exp.timezone)
        except Exception:
            logger.warning(
                "%s/%s: timezone %r inválido, expectativa ignorada",
                exp.source, exp.natural_key, exp.timezone,
            )
            continue
        now_local = now.astimezone(tz)

        if exp.schedule_kind == "INTERVAL":
            last_observed = _last_observed(conn, exp.source, exp.natural_key)
            if last_observed is None:
                # Nunca observado: sem observação nenhuma como ponto de
                # partida, não há "última ocorrência esperada" para medir
                # contra — ausência de dado não é ausência de execução.
                logger.info(
                    "%s/%s: INTERVAL sem nenhuma execução observada ainda, "
                    "não avaliado", exp.source, exp.natural_key,
                )
                continue
            deadline = (
                last_observed + _interval_timedelta(exp)
                + timedelta(minutes=exp.tolerance_minutes)
            )
            if now < deadline:
                continue
        else:
            due = _last_due(exp, now_local)
            if due is None:
                logger.warning(
                    "%s/%s: nenhuma ocorrência esperada nos últimos %d dias — "
                    "schedule_kind=%s possivelmente mal configurado",
                    exp.source, exp.natural_key, _LOOKBACK_DAYS, exp.schedule_kind,
                )
                continue
            deadline = due + timedelta(minutes=exp.tolerance_minutes)
            if now_local < deadline:
                continue
            last_observed = _last_observed(conn, exp.source, exp.natural_key)
            if last_observed is not None and last_observed >= due:
                continue  # rodou dentro da janela esperada

        resolution.resolve_event(
            conn,
            source=exp.source,
            result=c.OBSERVATION_FAIL,
            natural_key=exp.natural_key,
            key_rule=c.KEY_RULE_EXPECTATION,
            severity=c.DEFAULT_SEVERITY,
            error_code=c.ERROR_CODE_JOB_NOT_EXECUTED,
            error_description=(
                f"Execução esperada não observada (tolerância "
                f"{exp.tolerance_minutes} min)"
            ),
            observed_at=now,
        )
        opened += 1

    return opened


# ---------------------------------------------------------------------------
# Job travado
# ---------------------------------------------------------------------------

# Só Databricks: tbl_active_executions é a foto do que a Jobs API diz estar
# rodando agora (databricks.py) — não existe equivalente para ADF, que não
# expõe execução "em andamento" da mesma forma. O join usa o mesmo formato
# de natural_key que natural_key.py usa para JOB_RUN — workspace_id:job_id,
# minúsculo — e que sync_execution_expectations.py grava (collect_databricks_
# jobs). Sem isso os dois lados nunca casam e o job travado nunca é achado.
_STUCK_CANDIDATES = """
SELECT ae.workspace_id, ae.job_id, ae.run_id, ae.run_name, ae.started_at,
       e.max_expected_duration_min
  FROM tbl_active_executions AS ae
  JOIN tbl_cfg_execution_expectations AS e
    ON e.source = 'DATABRICKS'
   AND e.active = 1
   AND e.max_expected_duration_min IS NOT NULL
   AND e.natural_key = LOWER(CONCAT(ae.workspace_id, ':', ae.job_id))
 WHERE DATEADD(MINUTE, e.max_expected_duration_min, ae.started_at) < ?;
"""


def evaluate_stuck(
    conn: pyodbc.Connection, now: datetime | None = None
) -> int:
    """Abre `JOB_STUCK` para toda execução Databricks ativa cujo tempo
    corrido já ultrapassa `max_expected_duration_min` — sem esperar a
    execução terminar, porque pode nunca terminar sozinha. Devolve quantos
    incidentes foram abertos ou incrementados."""
    now = now or datetime.now(timezone.utc)
    rows = conn.cursor().execute(_STUCK_CANDIDATES, now).fetchall()

    for row in rows:
        natural_key = f"{row.workspace_id}:{row.job_id}".lower()
        resolution.resolve_event(
            conn,
            source=c.SOURCE_DATABRICKS,
            result=c.OBSERVATION_FAIL,
            natural_key=natural_key,
            key_rule=c.KEY_RULE_EXPECTATION,
            workspace_id=row.workspace_id,
            severity=c.DEFAULT_SEVERITY,
            error_code=c.ERROR_CODE_JOB_STUCK,
            error_description=(
                f"Em execução desde {row.started_at} — acima de "
                f"{row.max_expected_duration_min} min esperados"
            ),
            run_id=row.run_id,
            observed_at=now,
        )

    return len(rows)


def run_cycle(conn: pyodbc.Connection) -> tuple[int, int]:
    """Um ciclo do avaliador. Devolve (ausências abertas, travados abertos)."""
    expectations = list_active(conn)
    absent = evaluate_absence(conn, expectations)
    stuck = evaluate_stuck(conn)
    logger.info(
        "expectativas: %d ativas, %d ausências, %d travados",
        len(expectations), absent, stuck,
    )
    return absent, stuck
