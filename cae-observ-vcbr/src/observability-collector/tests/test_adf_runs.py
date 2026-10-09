"""Testes de pipeline run e trigger run do ADF (adf_runs.py).

O que importa aqui é a decisão de qual execução vira observação:

* pipeline `Failed` só abre incidente se NENHUMA atividade da execução falhou;
* pipeline `Failed` espera a carência de ingestão antes de ser avaliado;
* `Cancelled` só é registrado;
* chave estável do incidente (a do pipeline e a do trigger), para o `PASS`
  seguinte fechar.

A carência e as severidades são lidas de `constants`, nunca repetidas aqui.
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


def _stub(name: str, **attrs: object) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


_stub("httpx", Client=MagicMock(), HTTPStatusError=Exception)
_stub("pyodbc", connect=MagicMock(), Connection=object)
_stub("azure")
_stub("azure.identity", DefaultAzureCredential=MagicMock())

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

import adf_runs  # noqa: E402
import constants as c  # noqa: E402

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
GRACE = timedelta(minutes=c.PIPELINE_FAILURE_GRACE_MINUTES)
OLD = NOW - GRACE - timedelta(minutes=5)       # já passou da carência
RECENT = NOW - GRACE + timedelta(minutes=2)    # ainda dentro da carência


def pipeline_event(status, ended_at, run_id="run-1", pipeline="p1", ingested=None):
    return adf_runs.PipelineRunEvent(
        run_id=run_id, data_factory="DF-X-VCBR", pipeline_name=pipeline,
        status=status, error="boom" if status == c.ADF_STATUS_FAILED else None,
        ended_at=ended_at, ingested_at=ingested or ended_at,
    )


class TestSplitDeferred(unittest.TestCase):
    cutoff = NOW - GRACE

    def test_failed_inside_grace_waits(self):
        ready, deferred = adf_runs.split_deferred(
            [pipeline_event(c.ADF_STATUS_FAILED, RECENT)], self.cutoff)
        self.assertEqual((ready, len(deferred)), ([], 1))

    def test_failed_past_grace_is_ready(self):
        ready, deferred = adf_runs.split_deferred(
            [pipeline_event(c.ADF_STATUS_FAILED, OLD)], self.cutoff)
        self.assertEqual((len(ready), deferred), (1, []))

    def test_succeeded_never_waits(self):
        ready, _ = adf_runs.split_deferred(
            [pipeline_event(c.ADF_STATUS_SUCCEEDED, RECENT)], self.cutoff)
        self.assertEqual(len(ready), 1)

    def test_later_run_of_same_pipeline_waits_behind_a_deferred_failure(self):
        failed = pipeline_event(c.ADF_STATUS_FAILED, RECENT, run_id="a")
        later_ok = pipeline_event(
            c.ADF_STATUS_SUCCEEDED, RECENT + timedelta(minutes=1), run_id="b")
        ready, deferred = adf_runs.split_deferred([later_ok, failed], self.cutoff)
        self.assertEqual(ready, [])
        self.assertEqual([e.run_id for e in deferred], ["a", "b"])

    def test_other_pipeline_is_not_blocked(self):
        failed = pipeline_event(c.ADF_STATUS_FAILED, RECENT, pipeline="p1")
        other = pipeline_event(c.ADF_STATUS_SUCCEEDED, RECENT, pipeline="p2", run_id="z")
        ready, _ = adf_runs.split_deferred([failed, other], self.cutoff)
        self.assertEqual([e.run_id for e in ready], ["z"])


class TestNextWatermark(unittest.TestCase):
    def test_does_not_pass_a_deferred_row(self):
        current = NOW - timedelta(hours=1)
        ready = [pipeline_event(c.ADF_STATUS_SUCCEEDED, OLD, ingested=NOW)]
        deferred = [pipeline_event(c.ADF_STATUS_FAILED, RECENT, ingested=OLD)]
        result = adf_runs.next_watermark(current, ready, deferred)
        self.assertLess(result, deferred[0].ingested_at)

    def test_never_goes_backwards(self):
        current = NOW
        deferred = [pipeline_event(c.ADF_STATUS_FAILED, RECENT, ingested=OLD)]
        self.assertEqual(adf_runs.next_watermark(current, [], deferred), current)

    def test_advances_to_latest_processed(self):
        ready = [pipeline_event(c.ADF_STATUS_SUCCEEDED, OLD, ingested=NOW)]
        self.assertEqual(
            adf_runs.next_watermark(NOW - timedelta(hours=1), ready, []), NOW)


class TestRunPipelineCycle(unittest.TestCase):
    def _run(self, events, explained=frozenset(), is_new=True):
        with patch("adf_runs.query_pipeline_runs", return_value=events), \
             patch("adf_runs.query_failed_activity_pipeline_runs",
                   return_value=set(explained)) as explain, \
             patch("adf_runs.resolution") as resolution:
            resolution.read_watermark.return_value = NOW - timedelta(hours=1)
            resolution.log_execution.return_value = is_new
            count = adf_runs.run_pipeline_cycle(MagicMock(), MagicMock(), "ws", now=NOW)
        return count, resolution, explain

    def test_failed_without_failed_activity_opens_incident_with_stable_key(self):
        _, resolution, _ = self._run([pipeline_event(c.ADF_STATUS_FAILED, OLD)])
        kwargs = resolution.resolve_event.call_args.kwargs
        self.assertEqual(kwargs["result"], c.OBSERVATION_FAIL)
        self.assertEqual(kwargs["natural_key"], "df-x-vcbr/p1/pipeline")
        self.assertEqual(kwargs["error_code"], c.ERROR_CODE_ADF_PIPELINE_FAILED)
        self.assertEqual(kwargs["key_rule"], c.KEY_RULE_ADF_PIPELINE)
        self.assertNotIn("correlation_ref", kwargs)
        self.assertIn("boom", kwargs["error_description"])

    def test_failed_explained_by_an_activity_does_not_open_incident(self):
        _, resolution, _ = self._run(
            [pipeline_event(c.ADF_STATUS_FAILED, OLD, run_id="r1")], explained={"r1"})
        resolution.resolve_event.assert_not_called()
        resolution.log_execution.assert_called_once()  # a execução é registrada

    def test_succeeded_sends_pass_with_same_key(self):
        _, resolution, explain = self._run([pipeline_event(c.ADF_STATUS_SUCCEEDED, OLD)])
        kwargs = resolution.resolve_event.call_args.kwargs
        self.assertEqual(kwargs["result"], c.OBSERVATION_PASS)
        self.assertEqual(kwargs["natural_key"], "df-x-vcbr/p1/pipeline")
        explain.assert_not_called()  # só pipeline falho consulta atividades

    def test_cancelled_is_only_logged(self):
        _, resolution, _ = self._run([pipeline_event(c.ADF_STATUS_CANCELLED, OLD)])
        resolution.log_execution.assert_called_once()
        resolution.resolve_event.assert_not_called()

    def test_already_seen_run_is_not_resolved_again(self):
        _, resolution, _ = self._run(
            [pipeline_event(c.ADF_STATUS_FAILED, OLD)], is_new=False)
        resolution.resolve_event.assert_not_called()

    def test_failure_inside_grace_is_neither_logged_nor_resolved(self):
        count, resolution, _ = self._run([pipeline_event(c.ADF_STATUS_FAILED, RECENT)])
        self.assertEqual(count, 0)
        resolution.log_execution.assert_not_called()
        resolution.resolve_event.assert_not_called()

    def test_source_event_id_is_deterministic(self):
        _, first, _ = self._run([pipeline_event(c.ADF_STATUS_FAILED, OLD)])
        _, second, _ = self._run([pipeline_event(c.ADF_STATUS_FAILED, OLD)])
        self.assertEqual(
            first.resolve_event.call_args.kwargs["source_event_id"],
            second.resolve_event.call_args.kwargs["source_event_id"])


class TestQueryPipelineRuns(unittest.TestCase):
    def _payload(self, rows):
        names = ["TimeGenerated", "IngestedAt", "RunId", "DataFactoryName",
                 "PipelineName", "Status", "End", "ErrorCode", "ErrorMessage"]
        return {"tables": [{"columns": [{"name": n} for n in names], "rows": rows}]}

    @patch("adf_runs.query_log_analytics")
    def test_run_id_is_lowercased_and_end_falls_back_to_time_generated(self, mock_query):
        mock_query.return_value = self._payload([[
            "2026-10-07T11:00:00Z", "2026-10-07T11:01:00Z", "ABC-1", "DF-X-VCBR",
            "p1", "Failed", None, "BadRequest", "parametro inválido"]])
        events = adf_runs.query_pipeline_runs(MagicMock(), "ws", NOW)
        self.assertEqual(events[0].run_id, "abc-1")
        self.assertEqual(events[0].ended_at, datetime(2026, 10, 7, 11, 0, tzinfo=timezone.utc))
        self.assertEqual(events[0].error, "BadRequest: parametro inválido")

    @patch("adf_runs.query_log_analytics")
    def test_row_without_run_id_is_skipped(self, mock_query):
        mock_query.return_value = self._payload([[
            "2026-10-07T11:00:00Z", "2026-10-07T11:01:00Z", None, "DF", "p",
            "Failed", None, None, None]])
        self.assertEqual(adf_runs.query_pipeline_runs(MagicMock(), "ws", NOW), [])

    @patch("adf_runs.query_log_analytics")
    def test_ids_that_are_not_guid_like_never_reach_the_query(self, mock_query):
        result = adf_runs.query_failed_activity_pipeline_runs(
            MagicMock(), "ws", ['x" or 1==1 //'], NOW)
        self.assertEqual(result, set())
        mock_query.assert_not_called()


class TestRunTriggerCycle(unittest.TestCase):
    def _event(self, status):
        return adf_runs.TriggerRunEvent(
            data_factory="DF-X-VCBR", trigger_name="Diario", status=status,
            error="sem permissão" if status == c.ADF_STATUS_FAILED else None,
            started_at=OLD, ingested_at=NOW)

    def _run(self, status):
        with patch("adf_runs.query_trigger_runs", return_value=[self._event(status)]), \
             patch("adf_runs.resolution") as resolution:
            resolution.read_watermark.return_value = NOW - timedelta(hours=1)
            adf_runs.run_trigger_cycle(MagicMock(), MagicMock(), "ws")
        return resolution

    def test_failed_trigger_opens_incident_with_stable_key(self):
        kwargs = self._run(c.ADF_STATUS_FAILED).resolve_event.call_args.kwargs
        self.assertEqual(kwargs["result"], c.OBSERVATION_FAIL)
        self.assertEqual(kwargs["natural_key"], "df-x-vcbr/trigger/diario")
        self.assertEqual(kwargs["error_code"], c.ERROR_CODE_ADF_TRIGGER_FAILED)
        self.assertIn("sem permissão", kwargs["error_description"])

    def test_succeeded_trigger_sends_pass(self):
        resolution = self._run(c.ADF_STATUS_SUCCEEDED)
        self.assertEqual(
            resolution.resolve_event.call_args.kwargs["result"], c.OBSERVATION_PASS)

    def test_watermark_advances_to_ingestion_time(self):
        resolution = self._run(c.ADF_STATUS_FAILED)
        resolution.advance_watermark.assert_called_once()
        self.assertEqual(resolution.advance_watermark.call_args.args[2], NOW)


if __name__ == "__main__":
    unittest.main()
