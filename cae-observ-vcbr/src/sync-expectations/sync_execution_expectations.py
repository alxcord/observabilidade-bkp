"""
Observabilidade — Sincronização de tbl_cfg_execution_expectations (correção V6).

Sem este job, tbl_cfg_execution_expectations nasce vazia e a checagem de
"ausência de execução" — o gap funcional que motivou o cenário — nunca
dispara. Este é o motivo de ele existir: uma tabela preenchida à mão fica
vazia ou desatualizada.

O que faz, uma vez por dia:
  1. lê os jobs agendados de cada workspace Databricks (jobs/list) e extrai
     schedule.quartz_cron_expression + timezone_id;
  2. lê os triggers de agendamento de cada Data Factory e converte a
     recorrência em expressão cron equivalente;
  3. faz upsert de cada fonte em tbl_cfg_execution_expectations, com
     `config_source = 'AUTO'` — só atualiza e desativa as linhas que ela
     mesma criou;
  4. desativa (active=0) o que sumiu da respectiva fonte, sem apagar — para
     não perder histórico nem reabrir a linha por engano no dia seguinte.

Job que roda por agendamento **externo** ao Databricks e ao ADF (por
exemplo, disparado pelo Control-M via `run-now`) não tem `schedule` nativo
em nenhuma das duas APIs acima, então os passos 1 e 2 nunca o descobrem.
Esse caso é cadastrado direto em tbl_cfg_execution_expectations com
`config_source = 'MANUAL'`, via CRUD do observability-api — nunca lido nem
escrito por este módulo, e nunca tocado pela sincronização AUTO (ver MERGE
em `persist`). Isto substitui o antigo YAML versionado no repositório
Azure Repos `OBSERV-CONFIG`, lido em tempo de execução pela Items API: a
dependência foi eliminada (ver doc/11-sync-expectations.md).

Autenticação: Managed Identity atribuída pelo usuário, sem segredo. Ver §7.

  IMPORTANTE — por que o token não vem do SDK do Databricks aqui:
  o fluxo `azure_use_msi=true` do SDK assume o endpoint IMDS de VM
  (169.254.169.254). Container Apps expõe a identidade por IDENTITY_ENDPOINT/
  IDENTITY_HEADER, que é outro protocolo. `azure-identity` conhece os dois e
  resolve o correto em qualquer host, então pedimos o token por ele e o
  passamos como Bearer. Rodando em VM os dois caminhos funcionam; em Container
  Apps, só este.

Execução: Container Apps Job (mesma imagem do coletor, outro entrypoint) ou
job Databricks. Idempotente — pode rodar mais de uma vez no mesmo dia.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable

import httpx
import pyodbc
from azure.identity import DefaultAzureCredential

logger = logging.getLogger("sync-execution-expectations")

from constants import (
    AZURE_SCOPE,
    CONFIG_SOURCE_AUTO,
    DATABRICKS_SCOPE,
    DEFAULT_TIMEZONE,
    DEFAULT_TOLERANCE_MIN,
    SQL_SCOPE,
    WRITTEN_BY_ADF,
    WRITTEN_BY_DATABRICKS,
)


@dataclass(frozen=True)
class Expectation:
    """Uma expectativa já normalizada — ver schedule_kind.

    Só os campos do bloco correspondente a `schedule_kind` são preenchidos:
    INTERVAL usa interval_unit/interval_value; TIME_OF_DAY usa time_of_day
    e, opcionalmente, days_of_week; MONTHLY usa day_of_month ou
    last_day_of_month, mais time_of_day. O avaliador de ausência (§4.4) lê
    só estes campos — nunca uma expressão de cron.
    """

    source: str
    natural_key: str
    description: str | None
    schedule_kind: str  # 'INTERVAL' | 'TIME_OF_DAY' | 'MONTHLY'
    interval_unit: str | None
    interval_value: int | None
    time_of_day: str | None  # "HH:MM:SS"
    days_of_week: str | None  # CSV 'MON,TUE,...' — None = todo dia
    day_of_month: int | None
    last_day_of_month: bool
    observes_national_holidays: bool
    timezone: str
    tolerance_minutes: int
    max_expected_duration_min: int | None
    written_by: str
    source_expression: str | None  # cron/recurrence original — só auditoria


def _credential() -> DefaultAzureCredential:
    # client_id explícito: com mais de uma identidade atribuída ao host, a
    # resolução automática é ambígua e falha em runtime, não no deploy.
    client_id = os.environ.get("AZURE_CLIENT_ID")
    return DefaultAzureCredential(managed_identity_client_id=client_id)


# ---------------------------------------------------------------------------
# Databricks
# ---------------------------------------------------------------------------

def collect_databricks_jobs(
    credential: DefaultAzureCredential, workspace_id: str, workspace_url: str
) -> list[Expectation]:
    """Lê jobs/list e devolve os que têm agendamento ativo.

    Só jobs com schedule são expectativa de execução: um job disparado por
    ADF, por Control-M ou manualmente não "deveria ter rodado" em horário
    nenhum aos olhos desta API, e cadastrá-lo aqui produziria alerta de
    ausência para execução que nunca foi prometida por este caminho — é
    exatamente o caso que `config_source = 'MANUAL'` existe para cobrir à
    parte (ver docstring do módulo).

    `natural_key` leva o prefixo `workspace_id:` — o mesmo formato que
    `natural_key.py._resolve_job()` usa para identificar um `JOB_RUN` no
    coletor (só `job_id` não é globalmente único entre workspaces). Sem o
    prefixo, o avaliador de ausência (`expectations.py`) nunca casa a
    expectativa com nenhuma execução observada, e todo job agendado
    apareceria ausente sempre.
    """
    token = credential.get_token(DATABRICKS_SCOPE).token
    expectations: list[Expectation] = []
    page_token: str | None = None

    with httpx.Client(base_url=workspace_url, timeout=60) as client:
        while True:
            params: dict[str, object] = {"limit": 100, "expand_tasks": False}
            if page_token:
                params["page_token"] = page_token

            resp = client.get(
                "/api/2.2/jobs/list",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            payload = resp.json()

            for job in payload.get("jobs", []):
                settings = job.get("settings", {})
                schedule = settings.get("schedule") or {}

                # PAUSED conta como sem expectativa: o dono pausou de propósito.
                if schedule.get("pause_status") == "PAUSED":
                    continue

                cron = schedule.get("quartz_cron_expression")
                if not cron:
                    continue

                normalized = _quartz_to_schedule(cron)
                if normalized is None:
                    # Expressão fora do que normalizamos (múltiplos horários
                    # no dia, "#", segundo != 0, ...). Não normalizável não
                    # é erro: registra e não gera expectativa, em vez de
                    # arriscar um alerta de ausência calculado errado.
                    logger.warning(
                        "job %s (%s): cron %r não normalizado, sem expectativa",
                        job["job_id"], settings.get("name"), cron,
                    )
                    continue

                expectations.append(
                    Expectation(
                        source="DATABRICKS",
                        natural_key=f"{workspace_id}:{job['job_id']}".lower(),
                        description=settings.get("name"),
                        timezone=schedule.get("timezone_id", DEFAULT_TIMEZONE),
                        tolerance_minutes=DEFAULT_TOLERANCE_MIN,
                        max_expected_duration_min=_timeout_to_minutes(settings),
                        observes_national_holidays=True,  # Databricks não declara isto
                        written_by=WRITTEN_BY_DATABRICKS,
                        source_expression=cron,
                        **normalized,
                    )
                )

            page_token = payload.get("next_page_token")
            if not page_token:
                break

    logger.info("workspace %s: %d jobs agendados", workspace_url, len(expectations))
    return expectations


def _timeout_to_minutes(settings: dict) -> int | None:
    """Usa o timeout declarado do job como duração máxima esperada.

    É a melhor aproximação disponível sem histórico: se o dono do job declarou
    um timeout, ele já definiu quanto tempo considera demais. Quando não há
    timeout, fica NULL e a detecção de job travado simplesmente não se aplica
    àquele job — preferível a inventar um limite e gerar falso positivo.
    """
    seconds = settings.get("timeout_seconds")
    if not seconds:
        return None
    return max(1, int(seconds) // 60)


_QUARTZ_WEEKDAYS = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}


def _quartz_to_schedule(cron: str) -> dict | None:
    """Normaliza um cron Quartz (Databricks) nos campos de Expectation.

    Quartz tem 6 ou 7 campos — segundo, minuto, hora, dia-do-mês, mês,
    dia-da-semana, [ano] — e um cron comum de 5 campos interpreta a
    string errado (por exemplo, lê o segundo como se fosse o minuto).
    Cobre só os formatos que o Databricks realmente gera para os padrões
    de agendamento mais comuns: intervalo em minutos ou horas, um
    horário fixo por dia (com ou sem filtro de dia da semana), ou um dia
    fixo do mês (incluindo `L`, o último dia). Fora disso — múltiplos
    valores em minuto/hora, `#`, segundo diferente de zero — devolve
    None de propósito: expectativa mal interpretada gera alerta de
    ausência falso, e isso é pior que não ter expectativa nenhuma.
    """
    fields = cron.split()
    if len(fields) not in (6, 7):
        return None
    second, minute, hour, dom, month, dow = fields[:6]

    if second != "0" or month != "*":
        return None

    # Intervalo em minutos: "0 */N * * * ?"
    if minute.startswith("*/") and hour == "*" and dom in ("*", "?"):
        return {
            "schedule_kind": "INTERVAL",
            "interval_unit": "MINUTE",
            "interval_value": int(minute[2:]),
            "time_of_day": None,
            "days_of_week": None,
            "day_of_month": None,
            "last_day_of_month": False,
        }

    # Intervalo em horas, minuto fixo: "0 M */N * * ?"
    if minute.isdigit() and hour.startswith("*/") and dom in ("*", "?"):
        return {
            "schedule_kind": "INTERVAL",
            "interval_unit": "HOUR",
            "interval_value": int(hour[2:]),
            "time_of_day": None,
            "days_of_week": None,
            "day_of_month": None,
            "last_day_of_month": False,
        }

    if not (minute.isdigit() and hour.isdigit()):
        return None
    time_of_day = f"{int(hour):02d}:{int(minute):02d}:00"

    # Mensal: dia fixo do mês, ou "L" (último dia). Quartz exige dow='?'.
    if dow == "?" and dom not in ("*", "?"):
        if dom == "L":
            return {
                "schedule_kind": "MONTHLY", "interval_unit": None,
                "interval_value": None, "time_of_day": time_of_day,
                "days_of_week": None, "day_of_month": None,
                "last_day_of_month": True,
            }
        if dom.isdigit():
            return {
                "schedule_kind": "MONTHLY", "interval_unit": None,
                "interval_value": None, "time_of_day": time_of_day,
                "days_of_week": None, "day_of_month": int(dom),
                "last_day_of_month": False,
            }
        return None

    # Diário/semanal: horário fixo, com filtro opcional de dia da semana.
    if dom in ("*", "?"):
        days_of_week = None
        if dow not in ("*", "?"):
            days = dow.split(",")
            if not all(d in _QUARTZ_WEEKDAYS for d in days):
                return None
            days_of_week = ",".join(days)
        return {
            "schedule_kind": "TIME_OF_DAY", "interval_unit": None,
            "interval_value": None, "time_of_day": time_of_day,
            "days_of_week": days_of_week, "day_of_month": None,
            "last_day_of_month": False,
        }

    return None


# ---------------------------------------------------------------------------
# Azure Data Factory
# ---------------------------------------------------------------------------

def collect_adf_triggers(
    credential: DefaultAzureCredential, subscription_id: str, factories: Iterable[str]
) -> list[Expectation]:
    """Lê os triggers de agendamento de cada Data Factory.

    Apenas ScheduleTrigger entra: TumblingWindowTrigger e BlobEventsTrigger não
    representam horário fixo esperado, e tratá-los como tal geraria ausência
    falsa toda vez que o evento de origem simplesmente não ocorreu.
    """
    token = credential.get_token(AZURE_SCOPE).token
    expectations: list[Expectation] = []

    with httpx.Client(base_url="https://management.azure.com", timeout=60) as client:
        for factory in factories:
            resp = client.post(
                f"/subscriptions/{subscription_id}/resourceGroups/"
                f"{os.environ['ADF_RESOURCE_GROUP']}/providers/Microsoft.DataFactory/"
                f"factories/{factory}/triggers/querytriggers",
                params={"api-version": "2018-06-01"},
                headers={"Authorization": f"Bearer {token}"},
                json={},
            )
            resp.raise_for_status()

            for trigger in resp.json().get("value", []):
                props = trigger.get("properties", {})
                if props.get("type") != "ScheduleTrigger":
                    continue
                if props.get("runtimeState") != "Started":
                    continue

                recurrence = props.get("typeProperties", {}).get("recurrence", {})
                normalized = _recurrence_to_schedule(recurrence)
                if normalized is None:
                    continue

                # Um trigger dispara N pipelines; cada um é uma expectativa
                # própria, porque cada um pode falhar sozinho.
                for pipeline_ref in props.get("pipelines", []):
                    name = pipeline_ref.get("pipelineReference",
                                            {}).get("referenceName")
                    if not name:
                        continue
                    expectations.append(
                        Expectation(
                            source="ADF",
                            natural_key=f"{factory}/{name}",
                            description=trigger.get("name"),
                            timezone=recurrence.get("timeZone", DEFAULT_TIMEZONE),
                            tolerance_minutes=DEFAULT_TOLERANCE_MIN,
                            max_expected_duration_min=None,
                            observes_national_holidays=True,  # ADF não declara isto
                            written_by=WRITTEN_BY_ADF,
                            source_expression=repr(recurrence),
                            **normalized,
                        )
                    )

    logger.info("ADF: %d expectativas de pipeline", len(expectations))
    return expectations


_ADF_WEEKDAY_TO_QUARTZ = {
    "Sunday": "SUN", "Monday": "MON", "Tuesday": "TUE", "Wednesday": "WED",
    "Thursday": "THU", "Friday": "FRI", "Saturday": "SAT",
}


def _recurrence_to_schedule(recurrence: dict) -> dict | None:
    """Normaliza a recorrência do ADF nos campos de Expectation.

    A recorrência do ADF já é estruturada (frequency + interval + schedule),
    não texto — não existe cron a interpretar, só mapear. Cobre os casos
    regulares (Hour/Day/Week em horário fixo). Recorrências com múltiplos
    horários por dia, ou frequência/intervalo fora do que normalizamos,
    devolvem None de propósito: melhor não ter expectativa do que ter uma
    errada, que produz alerta de ausência falso.
    """
    frequency = recurrence.get("frequency")
    interval = int(recurrence.get("interval", 1))
    schedule = recurrence.get("schedule") or {}

    hours = schedule.get("hours") or []
    minutes = schedule.get("minutes") or [0]
    week_days = schedule.get("weekDays") or []

    if len(hours) > 1 or len(minutes) > 1:
        return None
    minute = int(minutes[0])

    if frequency == "Hour" and not hours:
        return {
            "schedule_kind": "INTERVAL", "interval_unit": "HOUR",
            "interval_value": interval, "time_of_day": None,
            "days_of_week": None, "day_of_month": None,
            "last_day_of_month": False,
        }

    if frequency == "Day" and hours and interval == 1:
        return {
            "schedule_kind": "TIME_OF_DAY", "interval_unit": None,
            "interval_value": None,
            "time_of_day": f"{int(hours[0]):02d}:{minute:02d}:00",
            "days_of_week": None, "day_of_month": None,
            "last_day_of_month": False,
        }

    if frequency == "Week" and hours and week_days and interval == 1:
        days = [_ADF_WEEKDAY_TO_QUARTZ[d] for d in week_days
                if d in _ADF_WEEKDAY_TO_QUARTZ]
        if not days:
            return None
        return {
            "schedule_kind": "TIME_OF_DAY", "interval_unit": None,
            "interval_value": None,
            "time_of_day": f"{int(hours[0]):02d}:{minute:02d}:00",
            "days_of_week": ",".join(days), "day_of_month": None,
            "last_day_of_month": False,
        }

    return None


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

# `config_source` entra duas vezes como parâmetro — uma no MATCH (só
# atualiza o que esta mesma fonte gravou antes) e uma no INSERT (marca a
# linha nova) — para uma fonte nunca sobrescrever a linha de outra que
# aponte para a mesma chave. Ver a nota no topo do arquivo sobre por que
# YAML e MANUAL são fontes distintas mesmo as duas sobrevivendo à
# sincronização AUTO.
UPSERT = """
MERGE tbl_cfg_execution_expectations AS target
USING (SELECT ? AS source, ? AS natural_key, ? AS description,
              ? AS schedule_kind, ? AS interval_unit, ? AS interval_value,
              ? AS time_of_day, ? AS days_of_week, ? AS day_of_month,
              ? AS last_day_of_month, ? AS observes_national_holidays,
              ? AS timezone, ? AS tolerance_minutes,
              ? AS max_expected_duration_min, ? AS written_by,
              ? AS source_expression, ? AS config_source)
      AS src
   ON target.source = src.source
  AND target.natural_key = src.natural_key
WHEN MATCHED AND target.config_source = src.config_source THEN
  UPDATE SET description                 = src.description,
             schedule_kind               = src.schedule_kind,
             interval_unit               = src.interval_unit,
             interval_value              = src.interval_value,
             time_of_day                 = src.time_of_day,
             days_of_week                = src.days_of_week,
             day_of_month                = src.day_of_month,
             last_day_of_month           = src.last_day_of_month,
             observes_national_holidays  = src.observes_national_holidays,
             timezone                    = src.timezone,
             max_expected_duration_min   = src.max_expected_duration_min,
             written_by                  = src.written_by,
             source_expression           = src.source_expression,
             active                      = 1,
             updated_at                  = SYSDATETIMEOFFSET()
WHEN NOT MATCHED THEN
  INSERT (source, natural_key, description, schedule_kind, interval_unit,
          interval_value, time_of_day, days_of_week, day_of_month,
          last_day_of_month, observes_national_holidays, timezone,
          tolerance_minutes, max_expected_duration_min, written_by,
          source_expression, config_source, active)
  VALUES (src.source, src.natural_key, src.description, src.schedule_kind,
          src.interval_unit, src.interval_value, src.time_of_day,
          src.days_of_week, src.day_of_month, src.last_day_of_month,
          src.observes_national_holidays, src.timezone,
          src.tolerance_minutes, src.max_expected_duration_min,
          src.written_by, src.source_expression, src.config_source, 1);
"""

# Some da origem = deixa de ser expectativa, mas a linha não é apagada:
# preserva histórico e evita que uma indisponibilidade momentânea da API
# apague configuração. Só linhas da mesma fonte são afetadas.
#
# A lista de chaves entra como parâmetros posicionais, montados em tempo de
# execução — sem interpolar valor em SQL e sem depender de STRING_SPLIT.
DEACTIVATE_MISSING = """
UPDATE tbl_cfg_execution_expectations
   SET active = 0, updated_at = SYSDATETIMEOFFSET()
 WHERE source = ?
   AND config_source = ?
   AND active = 1
   AND natural_key NOT IN ({placeholders});
"""


def persist(
    conn: pyodbc.Connection,
    expectations: list[Expectation],
    source: str,
    config_source: str = CONFIG_SOURCE_AUTO,
) -> None:
    if not expectations:
        # Nenhuma expectativa devolvida quase sempre significa falha de leitura,
        # não que a empresa parou de agendar jobs. Desativar tudo aqui apagaria
        # a configuração inteira por causa de um timeout de API.
        logger.warning(
            "origem %s (%s) não devolveu expectativas — nada será alterado",
            source, config_source,
        )
        return

    cursor = conn.cursor()
    for e in expectations:
        cursor.execute(
            UPSERT,
            e.source, e.natural_key, e.description, e.schedule_kind,
            e.interval_unit, e.interval_value, e.time_of_day,
            e.days_of_week, e.day_of_month, e.last_day_of_month,
            e.observes_national_holidays, e.timezone, e.tolerance_minutes,
            e.max_expected_duration_min, e.written_by, e.source_expression,
            config_source,
        )

    keys = [e.natural_key for e in expectations]
    placeholders = ", ".join("?" for _ in keys)
    cursor.execute(
        DEACTIVATE_MISSING.format(placeholders=placeholders),
        source, config_source, *keys,
    )
    conn.commit()
    logger.info(
        "origem %s (%s): %d expectativas sincronizadas",
        source, config_source, len(expectations),
    )


def connect_sql(credential: DefaultAzureCredential) -> pyodbc.Connection:
    """Conexão por Managed Identity — sem usuário e senha em lugar nenhum.

    A connection string é montada a partir de SQL_SERVER_FQDN e
    SQL_DATABASE — os dois nomes que o Bicep deste projeto realmente
    provisiona (ver infra/main.bicep) — em vez de um único
    SQL_CONNECTION_STRING, que nunca chegou a ser definido ali.
    """
    import struct

    token = credential.get_token(SQL_SCOPE).token
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


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    credential = _credential()

    with connect_sql(credential) as conn:
        workspaces = [
            (row.workspace_id, row.workspace_url)
            for row in conn.cursor().execute(
                "SELECT workspace_id, workspace_url"
                " FROM tbl_cfg_workspaces WHERE active = 1"
            )
        ]

        databricks: list[Expectation] = []
        for workspace_id, url in workspaces:
            try:
                databricks.extend(
                    collect_databricks_jobs(credential, workspace_id, url)
                )
            except Exception:
                # Um workspace inacessível não pode impedir a sincronização dos
                # demais, mas some do conjunto — por isso o guarda de conjunto
                # vazio em persist() e a desativação restrita ao que veio.
                logger.exception("falha ao ler jobs do workspace %s", url)

        persist(conn, databricks, "DATABRICKS", CONFIG_SOURCE_AUTO)

        factories = [f for f in os.environ.get("ADF_FACTORIES", "").split(",") if f]
        if factories:
            try:
                adf = collect_adf_triggers(
                    credential, os.environ["AZURE_SUBSCRIPTION_ID"], factories
                )
                persist(conn, adf, "ADF", CONFIG_SOURCE_AUTO)
            except Exception:
                logger.exception("falha ao ler triggers do ADF")

        # Expectativas config_source='MANUAL' (job disparado por agendador
        # externo, ex.: Control-M) são cadastradas direto em
        # tbl_cfg_execution_expectations via CRUD do observability-api —
        # este módulo nunca lê nem escreve essa fonte (ver docstring).


if __name__ == "__main__":
    main()
