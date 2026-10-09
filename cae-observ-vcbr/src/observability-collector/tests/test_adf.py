"""Testes do sinal ADF (documento §3.2) — parsing de data/hora do Log
Analytics e dedup por ActivityRunId.

httpx, pyodbc e azure.identity stubados — o que importa aqui é a conversão
de formato e a decisão de qual evento vira observação, não os clientes.
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timezone
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

import adf  # noqa: E402


class TestParseKqlDatetime(unittest.TestCase):
    """A Logs Query API devolve até 7 dígitos de fração — um a mais do que
    datetime.fromisoformat aceita antes de truncar."""

    def test_z_suffix_becomes_utc(self):
        result = adf._parse_kql_datetime("2024-06-01T12:00:00Z")
        self.assertEqual(result, datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc))

    def test_seven_digit_fraction_is_truncated_to_microseconds(self):
        result = adf._parse_kql_datetime("2024-06-01T12:00:00.1234567Z")
        self.assertEqual(result.microsecond, 123456)

    def test_six_digit_fraction_passes_through(self):
        result = adf._parse_kql_datetime("2024-06-01T12:00:00.500000Z")
        self.assertEqual(result.microsecond, 500000)


class TestRowsAsDicts(unittest.TestCase):
    def test_empty_tables_returns_empty_list(self):
        self.assertEqual(adf.rows_as_dicts({"tables": []}), [])

    def test_converts_columns_and_rows(self):
        payload = {
            "tables": [{
                "columns": [{"name": "A"}, {"name": "B"}],
                "rows": [[1, "x"], [2, "y"]],
            }]
        }
        self.assertEqual(
            adf.rows_as_dicts(payload),
            [{"A": 1, "B": "x"}, {"A": 2, "B": "y"}],
        )


class TestQueryActivityRuns(unittest.TestCase):
    """A correlação com o Databricks (§3.4) depende do ActivityRunId estar
    em minúsculas — o mesmo tratamento que natural_key.py aplica do lado
    Databricks — e de linhas sem ActivityRunId serem descartadas."""

    def _payload(self, rows):
        return {
            "tables": [{
                "columns": [
                    {"name": "TimeGenerated"}, {"name": "ActivityRunId"},
                    {"name": "PipelineRunId"}, {"name": "DataFactoryName"},
                    {"name": "PipelineName"}, {"name": "ActivityName"},
                    {"name": "Status"}, {"name": "Error"},
                ],
                "rows": rows,
            }]
        }

    @patch("adf.query_log_analytics")
    def test_activity_run_id_is_lowercased(self, mock_query):
        mock_query.return_value = self._payload([[
            "2024-06-01T12:00:00Z", "ABC-123-DEF", "pr1", "DF-LOGISTICS-VCBR",
            "pipeline_x", "notebook_activity", "Failed", "erro qualquer",
        ]])
        events = adf.query_activity_runs(
            MagicMock(), "la-workspace", datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(events[0].activity_run_id, "abc-123-def")

    @patch("adf.query_log_analytics")
    def test_row_without_activity_run_id_is_skipped(self, mock_query):
        mock_query.return_value = self._payload([[
            "2024-06-01T12:00:00Z", None, "pr1", "DF-LOGISTICS-VCBR",
            "pipeline_x", "activity", "Succeeded", None,
        ]])
        events = adf.query_activity_runs(
            MagicMock(), "la-workspace", datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(events, [])


class TestRunCycleKeys(unittest.TestCase):
    """A correção do fechamento automático: a correlação do incidente de
    atividade é a chave estável, e o ActivityRunId só é registrado."""

    def _resolve_kwargs(self, status):
        import constants as c
        event = adf.AdfActivityEvent(
            activity_run_id="abc-123", pipeline_run_id="pr-1",
            data_factory="DF-X-VCBR", pipeline_name="pipeline_x",
            activity_name="copy", status=status, error="boom",
            time_generated=datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc),
        )
        with patch("adf.query_activity_runs", return_value=[event]),              patch("adf.resolution") as resolution:
            resolution.read_watermark.return_value = datetime(
                2026, 10, 7, 11, 0, tzinfo=timezone.utc)
            resolution.log_execution.return_value = True
            adf.run_cycle(MagicMock(), MagicMock(), "ws")
        return resolution.resolve_event.call_args.kwargs, c

    def test_correlation_comes_from_the_stable_key_not_the_run_id(self):
        kwargs, _ = self._resolve_kwargs("Failed")
        self.assertEqual(kwargs["natural_key"], "df-x-vcbr/pipeline_x/copy")
        self.assertNotIn("adf_activity_run_id", kwargs)

    def test_activity_run_id_is_recorded_as_correlation_ref(self):
        kwargs, _ = self._resolve_kwargs("Failed")
        self.assertEqual(kwargs["correlation_ref"], "abc-123")

    def test_success_sends_pass_with_the_same_stable_key(self):
        kwargs, c = self._resolve_kwargs("Succeeded")
        self.assertEqual(kwargs["result"], c.OBSERVATION_PASS)
        self.assertEqual(kwargs["natural_key"], "df-x-vcbr/pipeline_x/copy")
        self.assertIsNone(kwargs["error_code"])


if __name__ == "__main__":
    unittest.main()
