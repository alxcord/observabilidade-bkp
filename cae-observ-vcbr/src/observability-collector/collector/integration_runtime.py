"""Disponibilidade de nós do Self-Hosted Integration Runtime (SHIR).

Fonte: a métrica `IntegrationRuntimeAvailableNodeNumber` lida pela **API de
métricas do Azure Monitor**, por Data Factory, com a série dividida pela
dimensão do Integration Runtime.

Não passa pelo Log Analytics de propósito: o Diagnostic Settings exporta
métrica multidimensional achatada e agregada entre as dimensões, então o nome
do IR não chegaria ao workspace, e num ADF com dois SHIRs o valor viria
agregado — a queda de um nó ficaria diluída pelo outro IR, justamente o que
este monitor existe para detectar. A API divide por dimensão, não exige
`AllMetrics` ligado no ADF, não lê tabela compartilhada com outros times e não
custa ingestão.

Só entra o que está cadastrado em `tbl_cfg_integration_runtimes` (ADF dono do
SHIR, nome do IR, assinatura, resource group e número esperado de nós). Linked
IR não se cadastra: o sinal vem do ADF dono.

Um incidente por (ADF, IR), `natural_key = <factory>/ir/<nome>`, com quatro
estados:

| Estado      | Quando                                   | Severidade |
|-------------|------------------------------------------|------------|
| `OK`        | nós disponíveis >= esperado              | fecha      |
| `PARTIAL`   | menos nós que o esperado, mas ao menos 1 | `MEDIUM`   |
| `DOWN`      | nenhum nó disponível                     | `HIGH`     |
| `NO_SIGNAL` | nenhuma medição recente                  | `HIGH`     |

`NO_SIGNAL` é estado, e não um desvio à parte, para que a piora seja vista: um
IR que estava com um nó a menos e depois fica mudo passa de `MEDIUM` para
`HIGH` como qualquer outra transição. Pela métrica não há como distinguir "IR
parado" de "diagnóstico do ADF desligado", e nos dois casos quem opera precisa
agir, então a mensagem diz as duas hipóteses.

O evento sai na MUDANÇA de estado, nunca por medição: nó a menos durante
vários ciclos é um alerta só. Não há watermark — a janela é fixa
(`IR_LOOKBACK_MINUTES`) e o estado anterior vem do incidente aberto no banco.
Reprocessar medição já vista é inofensivo porque o `source_event_id` de cada
uma é determinístico, e o reenvio vira `DUPLICADO` em `resolve_event`.

Só disponibilidade: CPU, memória, fila e atraso de pickup ficam de fora de
propósito (sobrecarga não é objeto deste monitor).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import pyodbc
from azure.identity import DefaultAzureCredential

import constants as c
import resolution
from adf import _parse_kql_datetime
from adf_runs import event_id

logger = logging.getLogger("collector.integration_runtime")

STATE_OK = "OK"
STATE_PARTIAL = "PARTIAL"
STATE_DOWN = "DOWN"
STATE_NO_SIGNAL = "NO_SIGNAL"


@dataclass(frozen=True)
class IntegrationRuntime:
    data_factory: str
    name: str
    subscription_id: str
    resource_group: str
    expected_nodes: int

    @property
    def natural_key(self) -> str:
        return f"{self.data_factory}/ir/{self.name}".lower()


@dataclass(frozen=True)
class NodeSample:
    integration_runtime: str   # nome como a dimensão da métrica devolveu
    available_nodes: int
    sampled_at: datetime


def list_integration_runtimes(conn: pyodbc.Connection) -> list[IntegrationRuntime]:
    rows = conn.cursor().execute(
        "SELECT data_factory, integration_runtime, subscription_id,"
        "       resource_group, expected_nodes"
        "  FROM tbl_cfg_integration_runtimes WHERE active = 1"
    ).fetchall()
    return [
        IntegrationRuntime(
            data_factory=r.data_factory,
            name=r.integration_runtime,
            subscription_id=r.subscription_id,
            resource_group=r.resource_group,
            expected_nodes=int(r.expected_nodes),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# API de métricas
# ---------------------------------------------------------------------------

def query_node_samples(
    credential: DefaultAzureCredential,
    runtime: IntegrationRuntime,
    since: datetime,
    until: datetime,
    timeout: float = 60,
) -> list[NodeSample]:
    """Medições de nós disponíveis do ADF do `runtime`, na janela pedida, já
    separadas por IR. Devolve as medições de TODOS os IRs daquele ADF; quem
    filtra pelo IR cadastrado é `samples_for`."""
    token = credential.get_token(c.ARM_SCOPE).token
    url = c.ARM_METRICS_URL.format(
        subscription_id=runtime.subscription_id,
        resource_group=runtime.resource_group,
        data_factory=runtime.data_factory,
    )
    params = {
        "api-version": c.ARM_METRICS_API_VERSION,
        "metricnames": c.METRIC_IR_AVAILABLE_NODES,
        "aggregation": c.METRIC_AGGREGATION_MINIMUM,
        "interval": c.METRIC_INTERVAL,
        "timespan": f"{_iso(since)}/{_iso(until)}",
        # Divide a série pela dimensão do IR; sem isto a API devolveria o
        # agregado do ADF inteiro.
        "$filter": f"{c.METRIC_DIMENSION_IR_NAME} eq '*'",
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.get(
            url, params=params, headers={"Authorization": f"Bearer {token}"}
        )
        response.raise_for_status()
        return parse_node_samples(response.json())


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_node_samples(payload: dict) -> list[NodeSample]:
    """Lê a resposta da API de métricas. Cada `timeseries` é um IR, nomeado em
    `metadatavalues`; cada ponto sem o campo da agregação é minuto sem
    medição, e é descartado — é a ausência deles que vira `NO_SIGNAL`."""
    aggregation = c.METRIC_AGGREGATION_MINIMUM.lower()
    dimension = c.METRIC_DIMENSION_IR_NAME.lower()
    samples: list[NodeSample] = []

    for metric in payload.get("value", []):
        for series in metric.get("timeseries", []):
            name = ""
            for meta in series.get("metadatavalues", []):
                # A API devolve o nome da dimensão em minúsculas.
                if str(meta.get("name", {}).get("value", "")).lower() == dimension:
                    name = str(meta.get("value") or "")
            for point in series.get("data", []):
                value = point.get(aggregation)
                if value is None:
                    continue
                samples.append(NodeSample(
                    integration_runtime=name,
                    available_nodes=int(round(float(value))),
                    sampled_at=_parse_kql_datetime(point["timeStamp"]),
                ))
    return samples


def samples_for(
    runtime: IntegrationRuntime, samples: list[NodeSample]
) -> list[NodeSample]:
    """Medições do IR cadastrado. Comparação sem diferenciar maiúsculas, como
    o resto das chaves da solução. Medição sem nome de IR fica de fora: a API
    deveria sempre nomear a dimensão, e atribuir às cegas geraria incidente no
    IR errado."""
    wanted = runtime.name.lower()
    return [s for s in samples if s.integration_runtime.lower() == wanted]


# ---------------------------------------------------------------------------
# Máquina de estado
# ---------------------------------------------------------------------------

def classify(available_nodes: int, expected_nodes: int) -> str:
    if available_nodes < 1:
        return STATE_DOWN
    if available_nodes < expected_nodes:
        return STATE_PARTIAL
    return STATE_OK


def observations(
    runtime: IntegrationRuntime, samples: list[NodeSample], now: datetime
) -> list[tuple[str, datetime, int | None]]:
    """Sequência `(estado, instante, nós)` em ordem de medição. Se a medição
    mais recente for mais velha que `IR_NO_SIGNAL_MINUTES` — ou se não houver
    medição nenhuma —, acrescenta `NO_SIGNAL` ao fim: é o estado atual, e as
    transições antigas que vierem antes dele seguem na sequência."""
    result = [
        (classify(s.available_nodes, runtime.expected_nodes), s.sampled_at,
         s.available_nodes)
        for s in sorted(samples, key=lambda s: s.sampled_at)
    ]
    deadline = now - timedelta(minutes=c.IR_NO_SIGNAL_MINUTES)
    if not result or result[-1][1] < deadline:
        result.append((STATE_NO_SIGNAL, now, None))
    return result


def open_incident_state(conn: pyodbc.Connection, natural_key: str) -> str | None:
    """Estado que o incidente aberto representa, ou `None` se não houver
    incidente aberto para este IR.

    Devolve `PARTIAL` para qualquer incidente de indisponibilidade, mesmo o que
    foi aberto com nenhum nó: `PARTIAL` e `DOWN` são os dois "degradado", e o
    que importa é não repetir o alerta de um estado que já está aberto nem
    perder a transição para `NO_SIGNAL` ou para `OK`.
    """
    # Mesma regra de correlação de event_resolution.resolve_event para
    # source = 'ADF' (doc/02): `adf:<natural_key>`.
    row = conn.cursor().execute(
        "SELECT error_code FROM tbl_problems"
        " WHERE correlation_key = ? AND incident_status = 'OPEN'",
        f"adf:{natural_key}".lower(),
    ).fetchone()
    if row is None:
        return None
    if row.error_code == c.ERROR_CODE_IR_NO_SIGNAL:
        return STATE_NO_SIGNAL
    return STATE_PARTIAL


def _local_time(moment: datetime) -> str:
    try:
        zone = ZoneInfo(c.DISPLAY_TIMEZONE)
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    return moment.astimezone(zone).strftime("%H:%M")


def describe(
    runtime: IntegrationRuntime, state: str, available_nodes: int | None,
    moment: datetime, started_here: bool,
) -> str:
    """Texto para quem opera a esteira: sem nome de métrica nem código."""
    head = f"Self-Hosted IR '{runtime.name}' (ADF {runtime.data_factory}): "
    when = _local_time(moment)

    if state == STATE_NO_SIGNAL:
        return (
            f"{head}sem nenhuma medição de nós há mais de "
            f"{c.IR_NO_SIGNAL_MINUTES} minutos. O IR pode estar parado ou o "
            f"diagnóstico do ADF foi desligado."
        )
    if state == STATE_DOWN:
        body = f"nenhum dos {runtime.expected_nodes} nós está disponível"
    else:
        missing = runtime.expected_nodes - (available_nodes or 0)
        body = (
            f"{missing} de {runtime.expected_nodes} "
            f"{'nó indisponível' if missing == 1 else 'nós indisponíveis'}"
        )
    # "desde" só quando a queda foi vista começando; numa leitura que continua
    # um incidente já aberto, a hora é a da medição, não a do início.
    return f"{head}{body} desde {when}" if started_here else (
        f"{head}{body} (última medição às {when})"
    )


def _severity(state: str) -> str:
    if state == STATE_DOWN:
        return c.IR_SEVERITY_NO_NODES
    if state == STATE_NO_SIGNAL:
        return c.IR_SEVERITY_NO_SIGNAL
    return c.IR_SEVERITY_PARTIAL


def _error_code(state: str) -> str:
    return (c.ERROR_CODE_IR_NO_SIGNAL if state == STATE_NO_SIGNAL
            else c.ERROR_CODE_IR_NODES_UNAVAILABLE)


def evaluate(
    conn: pyodbc.Connection,
    runtime: IntegrationRuntime,
    samples: list[NodeSample],
    now: datetime,
) -> int:
    """Emite um evento por MUDANÇA de estado. O estado de partida vem do banco:
    com incidente aberto parte-se do estado que ele representa, sem incidente
    parte-se de `OK`. Devolve quantos eventos emitiu."""
    opened = open_incident_state(conn, runtime.natural_key)
    previous = opened or STATE_OK
    # Com incidente aberto, o início real da queda ficou num ciclo anterior:
    # a mensagem não pode dizer "desde" a hora desta medição.
    previous_observed = opened is None
    emitted = 0

    for state, moment, nodes in observations(runtime, samples, now):
        if state == previous:
            previous_observed = True
            continue

        degraded = state != STATE_OK
        resolution.resolve_event(
            conn,
            source=c.SOURCE_ADF,
            result=c.OBSERVATION_FAIL if degraded else c.OBSERVATION_PASS,
            natural_key=runtime.natural_key,
            key_rule=c.KEY_RULE_IR_NODES,
            scope=runtime.data_factory,
            severity=_severity(state),
            error_code=_error_code(state) if degraded else None,
            error_description=(
                describe(runtime, state, nodes, moment,
                         started_here=previous_observed)
                if degraded else None
            ),
            source_event_id=event_id(
                "adf-ir", runtime.natural_key, moment.isoformat(), state
            ),
            observed_at=moment,
        )
        emitted += 1
        previous = state
        previous_observed = True
    return emitted


def run_cycle(
    conn: pyodbc.Connection,
    credential: DefaultAzureCredential,
    now: datetime | None = None,
) -> int:
    """Um ciclo do sinal de IR. Devolve quantos eventos foram emitidos.

    Uma chamada à API por Data Factory, não por IR: a resposta já vem dividida
    por dimensão, então todos os IRs do mesmo ADF saem de uma consulta."""
    now = now or datetime.now(timezone.utc)
    runtimes = list_integration_runtimes(conn)
    if not runtimes:
        logger.info("tbl_cfg_integration_runtimes sem nenhum IR ativo")
        return 0

    since = now - timedelta(minutes=c.IR_LOOKBACK_MINUTES)
    emitted = errors = 0

    for group in _by_factory(runtimes).values():
        factory = group[0].data_factory
        try:
            samples = query_node_samples(credential, group[0], since, now)
        except httpx.HTTPStatusError:
            # Um ADF inacessível não pode impedir a avaliação dos outros, e
            # tratar a falha como "sem sinal" abriria incidente no IR errado:
            # o problema é do coletor, não do IR.
            logger.exception(
                "falha ao consultar a métrica de nós do ADF %s", factory
            )
            errors += 1
            continue
        for runtime in group:
            emitted += evaluate(conn, runtime, samples_for(runtime, samples), now)

    logger.info(
        "IR: %d eventos de disponibilidade emitidos, %d Data Factory(ies) com falha "
        "de consulta", emitted, errors,
    )
    if errors:
        raise RuntimeError(
            f"{errors} Data Factory(ies) sem métrica de nós lida neste ciclo"
        )
    return emitted


def _by_factory(
    runtimes: list[IntegrationRuntime],
) -> dict[tuple[str, str, str], list[IntegrationRuntime]]:
    grouped: dict[tuple[str, str, str], list[IntegrationRuntime]] = {}
    for runtime in runtimes:
        key = (runtime.subscription_id, runtime.resource_group, runtime.data_factory)
        grouped.setdefault(key, []).append(runtime)
    return grouped
