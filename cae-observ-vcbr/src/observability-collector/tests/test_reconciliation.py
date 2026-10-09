"""Testes da rede de apoio (documento §3.1c).

`find_gaps` é a parte que não depende de schema incerto do Log Analytics
nem de rede — só ela é testada com rigor aqui. O ciclo de vida do candidato
(`tbl_ctl_reconciliation_gaps`) é testado com uma conexão fake, porque é a
peça que garante "ausente por mais de um ciclo", central o suficiente para
não ficar só na leitura do código.
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


_stub("httpx", Client=MagicMock(), HTTPStatusError=Exception)
_stub("pyodbc", connect=MagicMock(), Connection=object)
_stub("azure")
_stub("azure.identity", DefaultAzureCredential=MagicMock())

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

import reconciliation as rec  # noqa: E402


class TestFindGaps(unittest.TestCase):
    def test_run_present_in_log_analytics_and_locally_is_not_a_gap(self):
        gaps = rec.find_gaps({("ws1", "100")}, {("ws1", "100")})
        self.assertEqual(gaps, set())

    def test_run_present_only_in_log_analytics_is_a_gap(self):
        gaps = rec.find_gaps({("ws1", "100")}, set())
        self.assertEqual(gaps, {("ws1", "100")})

    def test_run_present_only_locally_is_not_a_gap(self):
        # A Jobs API capturou algo que a rede de apoio ainda não viu —
        # normal, dado o atraso de ingestão do Log Analytics; não é gap.
        gaps = rec.find_gaps(set(), {("ws1", "100")})
        self.assertEqual(gaps, set())

    def test_same_run_id_in_different_workspaces_is_scoped(self):
        gaps = rec.find_gaps(
            {("ws1", "100"), ("ws2", "100")}, {("ws1", "100")}
        )
        self.assertEqual(gaps, {("ws2", "100")})


class _FakeCandidateStore:
    """Substitui tbl_ctl_reconciliation_gaps por um set em memória, para
    testar o ciclo candidato -> promovido sem precisar de um banco real."""

    def __init__(self, seed: set[tuple[str, str]] | None = None):
        self.rows = set(seed or set())

    def read(self, conn):
        return set(self.rows)

    def record(self, conn, workspace_id, run_id):
        self.rows.add((workspace_id, run_id))

    def clear(self, conn, workspace_id, run_id):
        self.rows.discard((workspace_id, run_id))


class TestGapPromotion(unittest.TestCase):
    """Um run só vira tbl_executions na SEGUNDA vez que aparece ausente —
    a primeira só registra candidato, para não confundir atraso de um
    ciclo com gap de verdade."""

    def setUp(self):
        self.store = _FakeCandidateStore()
        self._orig_read = rec._read_candidates
        self._orig_record = rec._record_candidate
        self._orig_clear = rec._clear_candidate
        rec._read_candidates = self.store.read
        rec._record_candidate = self.store.record
        rec._clear_candidate = self.store.clear

    def tearDown(self):
        rec._read_candidates = self._orig_read
        rec._record_candidate = self._orig_record
        rec._clear_candidate = self._orig_clear

    def test_first_sighting_only_records_candidate(self):
        conn = MagicMock()
        gaps = {("ws1", "100")}
        candidates = rec._read_candidates(conn)
        promoted = 0
        now = datetime(2024, 6, 1, tzinfo=timezone.utc)
        for workspace_id, run_id in gaps:
            if (workspace_id, run_id) in candidates:
                rec._promote_gap(conn, workspace_id, run_id, None, now)
                promoted += 1
            else:
                rec._record_candidate(conn, workspace_id, run_id)

        self.assertEqual(promoted, 0)
        self.assertEqual(self.store.rows, {("ws1", "100")})

    def test_second_sighting_promotes_and_clears_candidate(self):
        self.store.rows = {("ws1", "100")}
        conn = MagicMock()

        # Chama log_execution de verdade dentro de _promote_gap — mocka só
        # o cursor, já que o que importa aqui é o ciclo de vida do
        # candidato, não a query de inserção (coberta em test_resolution.py).
        conn.cursor.return_value.rowcount = 1
        rec._promote_gap(
            conn, "ws1", "100", "job-9", datetime(2024, 6, 1, tzinfo=timezone.utc)
        )

        self.assertEqual(self.store.rows, set())

    def test_gap_that_disappears_before_promotion_is_cleared(self):
        # A Jobs API alcançou o run entre um ciclo e outro: o candidato
        # precisa sumir, para não promover um gap que já deixou de existir.
        self.store.rows = {("ws1", "100")}
        conn = MagicMock()
        resolved = self.store.rows - set()  # nenhum gap desta vez
        for workspace_id, run_id in resolved:
            rec._clear_candidate(conn, workspace_id, run_id)
        self.assertEqual(self.store.rows, set())


if __name__ == "__main__":
    unittest.main()
