"""Testes do publicador de eventos.

pyodbc, httpx e azure-identity são substituídos por stubs: o que importa
testar é o parsing da resposta da Statement Execution API, a validação de
`source_event_id` antes de montar o IN (...), e a orquestração de
`publish()` — não a biblioteca de terceiros nem um workspace Databricks
real. `event_resolution.resolve_event` é sempre mockado: sua própria
lógica é testada em `src/shared/tests/`.
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


_stub(
    "pyodbc", connect=MagicMock(), Connection=object,
    OperationalError=type("OperationalError", (Exception,), {}),
)
_stub("azure")
_stub("azure.identity", DefaultAzureCredential=MagicMock())
_stub("httpx", Client=MagicMock())

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import constants as c  # noqa: E402
import hub_publisher as hp  # noqa: E402


def make_row(source_id: str = "e1") -> dict:
    return {
        "source": "FRESHNESS",
        "result": "FAIL",
        "natural_key": "cat.sch.tbl",
        "domain": "Logistics",
        "severity": "HIGH",
        "error_code": "ASSET_STALE",
        "description": "Atrasado.",
        "source_event_id": source_id,
        "observed_at": datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
    }


class TestParseObservedAt(unittest.TestCase):
    def test_space_separated_without_timezone_assumes_utc(self):
        dt = hp._parse_observed_at("2026-09-04 12:00:00.000")
        self.assertEqual(dt, datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc))

    def test_iso_with_explicit_timezone_is_preserved(self):
        dt = hp._parse_observed_at("2026-09-04T12:00:00+00:00")
        self.assertEqual(dt, datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc))


class TestRowsFromResult(unittest.TestCase):
    def test_maps_data_array_by_declared_column_order(self):
        result = {
            "result": {
                "data_array": [
                    ["FRESHNESS", "FAIL", "cat.sch.tbl", "Logistics", "HIGH",
                     "ASSET_STALE", "Atrasado.", "e1", "2026-09-04 12:00:00.000"],
                ]
            }
        }
        rows = hp._rows_from_result(result)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "FRESHNESS")
        self.assertEqual(rows[0]["source_event_id"], "e1")
        self.assertEqual(
            rows[0]["observed_at"], datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
        )

    def test_empty_result_is_empty_list(self):
        self.assertEqual(hp._rows_from_result({"result": {}}), [])


class TestMarkSent(unittest.TestCase):
    def test_nothing_pending_never_calls_run_statement(self):
        with patch.object(hp, "run_statement") as run_statement:
            hp.mark_sent(MagicMock(), "t", "h", "w", [])
        run_statement.assert_not_called()

    def test_builds_in_clause_with_the_given_ids(self):
        with patch.object(hp, "run_statement") as run_statement:
            hp.mark_sent(MagicMock(), "t", "h", "w", ["e1", "e2"])
        statement = run_statement.call_args[0][-1]
        self.assertIn(c.STAGING_EVENTS_TABLE, statement)
        self.assertIn("'e1', 'e2'", statement)

    def test_rejects_id_outside_the_safe_alphabet(self):
        # source_event_id nunca deveria carregar isto, mas a validação
        # nunca pode depender só da origem ser interna.
        with self.assertRaises(ValueError):
            hp.mark_sent(MagicMock(), "t", "h", "w", ["e1'; DROP TABLE x; --"])


class TestPublish(unittest.TestCase):
    def _patched(self, events, resolve_side_effect):
        return (
            patch.object(hp, "read_pending_events", return_value=events),
            patch.object(hp, "resolve_event", side_effect=resolve_side_effect),
            patch.object(hp, "mark_sent"),
        )

    def test_marks_only_the_events_resolve_event_confirmed(self):
        events = [make_row("e1"), make_row("e2")]
        p1, p2, p3 = self._patched(events, [None, Exception("dado inválido")])
        with p1, p2, p3 as mark_sent:
            sent = hp.publish(MagicMock(), MagicMock(), "t", "h", "w", 10)

        self.assertEqual(sent, 1)
        mark_sent.assert_called_once()
        self.assertEqual(mark_sent.call_args[0][-1], ["e1"])

    def test_connection_failure_stops_the_cycle(self):
        events = [make_row("e1"), make_row("e2"), make_row("e3")]
        p1, p2, p3 = self._patched(events, [None, hp.pyodbc.OperationalError()])
        with p1, p2, p3 as mark_sent:
            sent = hp.publish(MagicMock(), MagicMock(), "t", "h", "w", 10)

        self.assertEqual(sent, 1)
        self.assertEqual(mark_sent.call_args[0][-1], ["e1"])

    def test_data_failure_does_not_stop_the_cycle(self):
        # Diferente de falha de conexão: um evento com dado ruim não pode
        # travar os demais do lote.
        events = [make_row("e1"), make_row("e2"), make_row("e3")]
        p1, p2, p3 = self._patched(events, [ValueError("origem desconhecida"), None, None])
        with p1, p2, p3 as mark_sent:
            sent = hp.publish(MagicMock(), MagicMock(), "t", "h", "w", 10)

        self.assertEqual(sent, 2)
        self.assertEqual(mark_sent.call_args[0][-1], ["e2", "e3"])

    def test_nothing_pending_still_calls_mark_sent_with_empty_list(self):
        client = MagicMock()
        p1, p2, p3 = self._patched([], [])
        with p1, p2, p3 as mark_sent:
            sent = hp.publish(MagicMock(), client, "t", "h", "w", 10)

        self.assertEqual(sent, 0)
        mark_sent.assert_called_once_with(client, "t", "h", "w", [])


if __name__ == "__main__":
    unittest.main()
