"""Testes da disponibilidade de nós do Self-Hosted IR (integration_runtime.py).

Cobre a leitura da resposta da API de métricas (série dividida por dimensão,
ponto sem valor), a decisão de estado (todos os nós / nó a menos / nenhum nó /
sem sinal), a emissão por MUDANÇA de estado, a escalada de degradado para sem
sinal, a recuperação e a mensagem para quem opera a esteira.

Prazos, severidades e códigos vêm de `constants`, nunca repetidos aqui.
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo


def _stub(name: str, **attrs: object) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


class _HttpStatusError(Exception):
    pass


_stub("httpx", Client=MagicMock(), HTTPStatusError=_HttpStatusError)
_stub("pyodbc", connect=MagicMock(), Connection=object)
_stub("azure")
_stub("azure.identity", DefaultAzureCredential=MagicMock())

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

import constants as c  # noqa: E402
import integration_runtime as ir  # noqa: E402

NOW = datetime(2026, 10, 7, 18, 0, 0, tzinfo=timezone.utc)
SILENCE = timedelta(minutes=c.IR_NO_SIGNAL_MINUTES)
EXPECTED = 2

RUNTIME = ir.IntegrationRuntime(
    data_factory="DF-X-VCBR", name="IR-VCBR-01",
    subscription_id="00000000-0000-0000-0000-000000000001",
    resource_group="RG-ANALYTICS-VCBR", expected_nodes=EXPECTED,
)
OTHER = ir.IntegrationRuntime(
    data_factory="DF-X-VCBR", name="IR-VCBR-02",
    subscription_id=RUNTIME.subscription_id,
    resource_group=RUNTIME.resource_group, expected_nodes=1,
)


def sample(minutes_ago, nodes, runtime="IR-VCBR-01"):
    """Medição de `nodes` nós, feita `minutes_ago` minutos antes de NOW."""
    return ir.NodeSample(runtime, nodes, NOW - timedelta(minutes=minutes_ago))


# ---------------------------------------------------------------------------
# Leitura da resposta da API de métricas
# ---------------------------------------------------------------------------

def payload(*series):
    return {"value": [{
        "name": {"value": c.METRIC_IR_AVAILABLE_NODES},
        "timeseries": list(series),
    }]}


def timeseries(ir_name, points):
    meta = ([{"name": {"value": c.METRIC_DIMENSION_IR_NAME.lower()}, "value": ir_name}]
            if ir_name is not None else [])
    return {"metadatavalues": meta, "data": points}


def point(timestamp, nodes=None):
    row = {"timeStamp": timestamp}
    if nodes is not None:
        row[c.METRIC_AGGREGATION_MINIMUM.lower()] = float(nodes)
    return row


class TestParseNodeSamples(unittest.TestCase):
    def test_reads_the_runtime_name_from_the_dimension(self):
        samples = ir.parse_node_samples(
            payload(timeseries("IR-VCBR-01", [point("2026-10-07T17:59:00Z", 2)])))
        self.assertEqual(samples[0].integration_runtime, "IR-VCBR-01")
        self.assertEqual(samples[0].available_nodes, 2)

    def test_point_without_value_is_a_minute_without_measurement(self):
        samples = ir.parse_node_samples(
            payload(timeseries("IR-VCBR-01", [
                point("2026-10-07T17:58:00Z"),
                point("2026-10-07T17:59:00Z", 1),
            ])))
        self.assertEqual([s.available_nodes for s in samples], [1])

    def test_each_timeseries_is_one_runtime(self):
        samples = ir.parse_node_samples(payload(
            timeseries("IR-VCBR-01", [point("2026-10-07T17:59:00Z", 2)]),
            timeseries("IR-VCBR-02", [point("2026-10-07T17:59:00Z", 0)]),
        ))
        self.assertEqual(
            {(s.integration_runtime, s.available_nodes) for s in samples},
            {("IR-VCBR-01", 2), ("IR-VCBR-02", 0)})

    def test_empty_response_is_no_samples(self):
        self.assertEqual(ir.parse_node_samples({"value": []}), [])

    def test_timestamp_is_parsed_as_utc(self):
        samples = ir.parse_node_samples(
            payload(timeseries("IR", [point("2026-10-07T17:59:00Z", 1)])))
        self.assertEqual(
            samples[0].sampled_at,
            datetime(2026, 10, 7, 17, 59, tzinfo=timezone.utc))


class TestSamplesFor(unittest.TestCase):
    def test_matches_the_registered_runtime_ignoring_case(self):
        picked = ir.samples_for(RUNTIME, [sample(1, 2, runtime="ir-vcbr-01")])
        self.assertEqual(len(picked), 1)

    def test_other_runtime_of_the_same_factory_is_left_out(self):
        self.assertEqual(ir.samples_for(RUNTIME, [sample(1, 0, runtime="IR-VCBR-02")]), [])

    def test_sample_without_a_runtime_name_is_not_guessed(self):
        self.assertEqual(ir.samples_for(RUNTIME, [sample(1, 0, runtime="")]), [])


class TestQueryShape(unittest.TestCase):
    """A consulta tem de pedir a série dividida pela dimensão do IR: sem isso,
    a API devolve o agregado do ADF inteiro e a queda de um nó se dilui."""

    def _params(self):
        response = MagicMock()
        response.json.return_value = {"value": []}
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.get.return_value = response
        with patch("integration_runtime.httpx.Client", return_value=client):
            ir.query_node_samples(MagicMock(), RUNTIME, NOW - timedelta(hours=1), NOW)
        return client.get.call_args

    def test_splits_the_series_by_the_runtime_dimension(self):
        params = self._params().kwargs["params"]
        self.assertEqual(params["$filter"], f"{c.METRIC_DIMENSION_IR_NAME} eq '*'")

    def test_asks_for_the_node_metric_with_the_minimum_aggregation(self):
        params = self._params().kwargs["params"]
        self.assertEqual(params["metricnames"], c.METRIC_IR_AVAILABLE_NODES)
        self.assertEqual(params["aggregation"], c.METRIC_AGGREGATION_MINIMUM)

    def test_url_points_to_the_registered_factory(self):
        url = self._params().args[0]
        self.assertIn(RUNTIME.subscription_id, url)
        self.assertIn(RUNTIME.resource_group, url)
        self.assertIn(RUNTIME.data_factory, url)


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------

class TestClassify(unittest.TestCase):
    def test_all_expected_nodes_is_ok(self):
        self.assertEqual(ir.classify(EXPECTED, EXPECTED), ir.STATE_OK)

    def test_more_than_expected_is_ok(self):
        self.assertEqual(ir.classify(EXPECTED + 1, EXPECTED), ir.STATE_OK)

    def test_one_node_short_is_partial(self):
        self.assertEqual(ir.classify(EXPECTED - 1, EXPECTED), ir.STATE_PARTIAL)

    def test_zero_nodes_is_down(self):
        self.assertEqual(ir.classify(0, EXPECTED), ir.STATE_DOWN)


class TestObservations(unittest.TestCase):
    def test_no_measurement_at_all_is_no_signal(self):
        self.assertEqual(
            [o[0] for o in ir.observations(RUNTIME, [], NOW)], [ir.STATE_NO_SIGNAL])

    def test_stale_measurement_is_followed_by_no_signal(self):
        stale = sample(c.IR_NO_SIGNAL_MINUTES + 1, EXPECTED)
        self.assertEqual(
            [o[0] for o in ir.observations(RUNTIME, [stale], NOW)],
            [ir.STATE_OK, ir.STATE_NO_SIGNAL])

    def test_recent_measurement_is_not_followed_by_no_signal(self):
        recent = sample(max(c.IR_NO_SIGNAL_MINUTES - 1, 0), EXPECTED)
        self.assertEqual(
            [o[0] for o in ir.observations(RUNTIME, [recent], NOW)], [ir.STATE_OK])

    def test_samples_come_in_chronological_order(self):
        out = ir.observations(RUNTIME, [sample(1, 0), sample(5, EXPECTED)], NOW)
        self.assertEqual([o[0] for o in out], [ir.STATE_OK, ir.STATE_DOWN])


class TestEvaluate(unittest.TestCase):
    def _run(self, samples, previous=None):
        with patch("integration_runtime.open_incident_state", return_value=previous), \
             patch("integration_runtime.resolution") as resolution:
            emitted = ir.evaluate(MagicMock(), RUNTIME, samples, NOW)
        return emitted, [call.kwargs for call in resolution.resolve_event.call_args_list]

    def test_healthy_runtime_emits_nothing(self):
        self.assertEqual(self._run([sample(1, EXPECTED)]), (0, []))

    def test_node_loss_opens_a_degraded_incident(self):
        _, calls = self._run([sample(1, EXPECTED - 1)])
        self.assertEqual(calls[0]["result"], c.OBSERVATION_FAIL)
        self.assertEqual(calls[0]["severity"], c.IR_SEVERITY_PARTIAL)
        self.assertEqual(calls[0]["error_code"], c.ERROR_CODE_IR_NODES_UNAVAILABLE)
        self.assertEqual(calls[0]["natural_key"], "df-x-vcbr/ir/ir-vcbr-01")
        self.assertEqual(calls[0]["key_rule"], c.KEY_RULE_IR_NODES)

    def test_zero_nodes_is_highest_severity(self):
        _, calls = self._run([sample(1, 0)])
        self.assertEqual(calls[0]["severity"], c.IR_SEVERITY_NO_NODES)

    def test_repeated_degraded_measurements_emit_once(self):
        short = EXPECTED - 1
        emitted, _ = self._run([sample(3, short), sample(2, short), sample(1, short)])
        self.assertEqual(emitted, 1)

    def test_worsening_emits_again_with_higher_severity(self):
        _, calls = self._run([sample(5, EXPECTED - 1), sample(1, 0)])
        self.assertEqual([call["severity"] for call in calls],
                         [c.IR_SEVERITY_PARTIAL, c.IR_SEVERITY_NO_NODES])

    def test_recovery_sends_pass_and_closes(self):
        _, calls = self._run([sample(5, 0), sample(1, EXPECTED)])
        self.assertEqual(calls[-1]["result"], c.OBSERVATION_PASS)
        self.assertIsNone(calls[-1]["error_description"])

    def test_no_measurement_opens_a_no_signal_incident(self):
        emitted, calls = self._run([])
        self.assertEqual(emitted, 1)
        self.assertEqual(calls[0]["result"], c.OBSERVATION_FAIL)
        self.assertEqual(calls[0]["severity"], c.IR_SEVERITY_NO_SIGNAL)
        self.assertEqual(calls[0]["error_code"], c.ERROR_CODE_IR_NO_SIGNAL)

    def test_degraded_incident_escalates_when_the_signal_disappears(self):
        """O buraco que a máquina de estado fecha: com um incidente de nó a
        menos aberto, o IR ficar mudo tem de gerar alerta novo e subir para a
        severidade máxima."""
        emitted, calls = self._run([], previous=ir.STATE_PARTIAL)
        self.assertEqual(emitted, 1)
        self.assertEqual(calls[0]["error_code"], c.ERROR_CODE_IR_NO_SIGNAL)
        self.assertEqual(calls[0]["severity"], c.IR_SEVERITY_NO_SIGNAL)

    def test_no_signal_is_not_repeated_while_the_incident_is_open(self):
        self.assertEqual(self._run([], previous=ir.STATE_NO_SIGNAL), (0, []))

    def test_degraded_is_not_repeated_while_the_incident_is_open(self):
        self.assertEqual(
            self._run([sample(1, EXPECTED - 1)], previous=ir.STATE_PARTIAL), (0, []))

    def test_signal_coming_back_healthy_closes_the_no_signal_incident(self):
        _, calls = self._run([sample(1, EXPECTED)], previous=ir.STATE_NO_SIGNAL)
        self.assertEqual(calls[0]["result"], c.OBSERVATION_PASS)

    def test_same_measurement_always_gets_the_same_event_id(self):
        _, first = self._run([sample(1, 0)])
        _, second = self._run([sample(1, 0)])
        self.assertEqual(first[0]["source_event_id"], second[0]["source_event_id"])


class TestOpenIncidentState(unittest.TestCase):
    def _state(self, row):
        conn = MagicMock()
        conn.cursor.return_value.execute.return_value.fetchone.return_value = row
        return ir.open_incident_state(conn, RUNTIME.natural_key)

    def test_no_open_incident(self):
        self.assertIsNone(self._state(None))

    def test_open_no_signal_incident(self):
        row = MagicMock(error_code=c.ERROR_CODE_IR_NO_SIGNAL)
        self.assertEqual(self._state(row), ir.STATE_NO_SIGNAL)

    def test_open_availability_incident_counts_as_degraded(self):
        row = MagicMock(error_code=c.ERROR_CODE_IR_NODES_UNAVAILABLE)
        self.assertEqual(self._state(row), ir.STATE_PARTIAL)

    def test_looks_up_by_the_same_correlation_rule_resolve_event_uses(self):
        conn = MagicMock()
        conn.cursor.return_value.execute.return_value.fetchone.return_value = None
        ir.open_incident_state(conn, RUNTIME.natural_key)
        self.assertEqual(
            conn.cursor.return_value.execute.call_args.args[1],
            f"adf:{RUNTIME.natural_key}")


# ---------------------------------------------------------------------------
# Mensagem
# ---------------------------------------------------------------------------

class TestDescribe(unittest.TestCase):
    local = NOW.astimezone(ZoneInfo(c.DISPLAY_TIMEZONE)).strftime("%H:%M")

    def test_partial_message_is_readable_and_has_no_metric_name(self):
        text = ir.describe(RUNTIME, ir.STATE_PARTIAL, EXPECTED - 1, NOW, True)
        self.assertEqual(
            text,
            f"Self-Hosted IR '{RUNTIME.name}' (ADF {RUNTIME.data_factory}): "
            f"1 de {EXPECTED} nó indisponível desde {self.local}")
        self.assertNotIn(c.METRIC_IR_AVAILABLE_NODES, text)

    def test_plural_when_more_than_one_node_is_missing(self):
        runtime = ir.IntegrationRuntime("DF", "IR", "sub", "rg", expected_nodes=4)
        self.assertIn("2 de 4 nós indisponíveis",
                      ir.describe(runtime, ir.STATE_PARTIAL, 2, NOW, True))

    def test_down_message(self):
        self.assertIn(f"nenhum dos {EXPECTED} nós está disponível",
                      ir.describe(RUNTIME, ir.STATE_DOWN, 0, NOW, True))

    def test_no_signal_message_offers_both_hypotheses(self):
        text = ir.describe(RUNTIME, ir.STATE_NO_SIGNAL, None, NOW, True)
        self.assertIn(f"{c.IR_NO_SIGNAL_MINUTES} minutos", text)
        self.assertIn("parado", text)
        self.assertIn("diagnóstico", text)

    def test_continuation_does_not_claim_a_start_time(self):
        text = ir.describe(RUNTIME, ir.STATE_PARTIAL, 1, NOW, False)
        self.assertNotIn("desde", text)
        self.assertIn("última medição", text)


# ---------------------------------------------------------------------------
# Ciclo
# ---------------------------------------------------------------------------

class TestRunCycle(unittest.TestCase):
    def test_no_registered_runtime_does_not_call_the_api(self):
        with patch("integration_runtime.list_integration_runtimes", return_value=[]), \
             patch("integration_runtime.query_node_samples") as query:
            self.assertEqual(ir.run_cycle(MagicMock(), MagicMock()), 0)
        query.assert_not_called()

    def test_one_call_per_factory_even_with_two_runtimes(self):
        with patch("integration_runtime.list_integration_runtimes",
                   return_value=[RUNTIME, OTHER]), \
             patch("integration_runtime.query_node_samples", return_value=[]) as query, \
             patch("integration_runtime.evaluate", return_value=0):
            ir.run_cycle(MagicMock(), MagicMock(), now=NOW)
        self.assertEqual(query.call_count, 1)

    def test_each_runtime_only_sees_its_own_measurements(self):
        samples = [sample(1, 0, runtime="IR-VCBR-01"),
                   sample(1, 1, runtime="IR-VCBR-02")]
        seen = {}
        with patch("integration_runtime.list_integration_runtimes",
                   return_value=[RUNTIME, OTHER]), \
             patch("integration_runtime.query_node_samples", return_value=samples), \
             patch("integration_runtime.evaluate",
                   side_effect=lambda conn, rt, s, now: seen.setdefault(rt.name, s) and 0):
            ir.run_cycle(MagicMock(), MagicMock(), now=NOW)
        self.assertEqual([s.available_nodes for s in seen["IR-VCBR-01"]], [0])
        self.assertEqual([s.available_nodes for s in seen["IR-VCBR-02"]], [1])

    def test_window_covers_more_than_one_cycle_of_the_job(self):
        with patch("integration_runtime.list_integration_runtimes", return_value=[RUNTIME]), \
             patch("integration_runtime.query_node_samples", return_value=[]) as query, \
             patch("integration_runtime.evaluate", return_value=0):
            ir.run_cycle(MagicMock(), MagicMock(), now=NOW)
        self.assertLessEqual(query.call_args.args[2], NOW - timedelta(minutes=c.OVERLAP_MINUTES))

    def test_unreachable_factory_raises_instead_of_claiming_no_signal(self):
        """Falha de consulta é problema do coletor, não do IR: tratá-la como
        silêncio abriria incidente no IR errado, e engolir o erro em silêncio é
        o que esta solução existe para eliminar."""
        with patch("integration_runtime.list_integration_runtimes", return_value=[RUNTIME]), \
             patch("integration_runtime.query_node_samples",
                   side_effect=_HttpStatusError("403")), \
             patch("integration_runtime.resolution") as resolution:
            with self.assertRaises(RuntimeError):
                ir.run_cycle(MagicMock(), MagicMock(), now=NOW)
        resolution.resolve_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
