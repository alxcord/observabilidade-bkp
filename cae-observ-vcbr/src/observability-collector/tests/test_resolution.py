"""Testes de resolution.py — o log append-only de execuções, os
watermarks, e a reexportação de `resolve_event()`.

A lógica de decisão de `resolve_event()` (abrir/incrementar/fechar
incidente) não é testada aqui: ela mora em `src/shared/event_resolution.py`
e tem sua própria suíte em `src/shared/tests/test_event_resolution.py`,
justamente porque é compartilhada com o publicador e não pode ficar amarrada aos
testes de um só consumidor.

pyodbc é substituído por um stub antes da importação (mesmo padrão de
test_sync_execution_expectations.py, do projeto irmão sync-expectations).
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock


def _stub(name: str, **attrs: object) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


_stub("pyodbc", connect=MagicMock(), Connection=object, IntegrityError=Exception)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

import resolution  # noqa: E402


class _Row:
    """Simula uma linha de cursor pyodbc — acesso por atributo, como o
    driver real devolve quando a query nomeia as colunas."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class TestResolveEventIsWiredToSharedModule(unittest.TestCase):
    """Não testa a lógica de resolve_event() (isso é
    src/shared/tests/test_event_resolution.py) — só garante que
    resolution.py de fato reexporta a função certa, e não uma cópia local
    que alguém tenha reintroduzido por engano."""

    def test_reexports_the_shared_function(self):
        import event_resolution
        self.assertIs(resolution.resolve_event, event_resolution.resolve_event)


class TestLogExecution(unittest.TestCase):
    """O log append-only precisa ser idempotente: a sobreposição
    deliberada da janela de recuperação (§3.1c) faz o mesmo run aparecer em
    mais de uma varredura, e sem essa guarda resolve_event() seria chamada
    duas vezes para o mesmo evento."""

    def test_new_observation_reports_true(self):
        conn = MagicMock()
        conn.cursor.return_value.rowcount = 1
        is_new = resolution.log_execution(
            conn, source="DATABRICKS", scope="ws1", natural_key="ws1:123",
            run_id="999", run_name="foo", status="TERMINATED",
            result_state="SUCCESS", capture_source="API",
            observed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        self.assertTrue(is_new)
        conn.commit.assert_called_once()

    def test_duplicate_observation_reports_false(self):
        # rowcount 0 é o que o INSERT...WHERE NOT EXISTS devolve quando a
        # linha já existia — a mesma PK (source, scope, run_id, observed_at)
        # já tinha sido gravada por uma varredura anterior.
        conn = MagicMock()
        conn.cursor.return_value.rowcount = 0
        is_new = resolution.log_execution(
            conn, source="DATABRICKS", scope="ws1", natural_key="ws1:123",
            run_id="999", run_name="foo", status="TERMINATED",
            result_state="SUCCESS", capture_source="API",
            observed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        self.assertFalse(is_new)


class TestWatermark(unittest.TestCase):
    def test_missing_watermark_returns_epoch(self):
        conn = MagicMock()
        conn.cursor.return_value.execute.return_value.fetchone.return_value = None
        watermark = resolution.read_watermark(conn, "DBX:ws1")
        self.assertEqual(watermark.year, 1900)

    def test_existing_watermark_is_returned(self):
        conn = MagicMock()
        expected = datetime(2024, 6, 1, tzinfo=timezone.utc)
        conn.cursor.return_value.execute.return_value.fetchone.return_value = _Row(
            watermark=expected
        )
        self.assertEqual(resolution.read_watermark(conn, "DBX:ws1"), expected)

    def test_advance_watermark_commits(self):
        conn = MagicMock()
        resolution.advance_watermark(
            conn, "DBX:ws1", datetime(2024, 6, 1, tzinfo=timezone.utc)
        )
        conn.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
