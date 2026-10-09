"""Testes do ciclo Databricks (documento §3.1).

Os dois testes que o README deste projeto lista como os que mais importam
estão aqui: a reconciliação de tbl_active_executions (um run ausente é
removido, e a remoção não vaza para outro workspace) e o piso da janela de
recuperação (ancorado na execução aberta mais antiga, nunca um lookback
fixo).

httpx, pyodbc e azure.identity são stubados antes da importação — mesmo
padrão de test_sync_execution_expectations.py e test_hub_replication.py:
o que importa testar é a lógica de reconciliação e de janela, não os
clientes de terceiros.
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock


def _stub(name: str, **attrs: object) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


_stub("httpx", Client=MagicMock())
_stub("pyodbc", connect=MagicMock(), Connection=object)
_stub("azure")
_stub("azure.identity", DefaultAzureCredential=MagicMock())

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

import databricks as dbx  # noqa: E402


class _Row:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _conn_with_active(existing_run_ids_by_workspace: dict[str, set[str]]) -> MagicMock:
    """Conexão fake cujo SELECT de tbl_active_executions responde por
    workspace — é essa distinção por workspace_id que o teste de "não vaza
    para outro workspace" precisa poder observar."""
    conn = MagicMock()
    cursor = conn.cursor.return_value

    def execute_side_effect(sql, *params):
        if "SELECT run_id FROM tbl_active_executions" in sql:
            workspace_id = params[0]
            run_ids = existing_run_ids_by_workspace.get(workspace_id, set())
            return [_Row(run_id=r) for r in run_ids]
        return cursor

    cursor.execute.side_effect = execute_side_effect
    return conn


class TestReconcileActiveExecutions(unittest.TestCase):
    """O coração do passo (a): sem apagar o que sumiu, toda execução
    concluída vira JOB_STUCK falso assim que passa da duração esperada."""

    def test_run_missing_from_response_is_removed(self):
        conn = _conn_with_active({"ws1": {"100", "200"}})
        cursor = conn.cursor.return_value

        # Só "100" segue ativo — "200" concluiu e sumiu da resposta.
        current_runs = {
            "100": {"job_id": "1", "run_name": "a", "state": {"life_cycle_state": "RUNNING"},
                    "start_time": 1_700_000_000_000}
        }
        closed = dbx.reconcile_active_executions(conn, "ws1", current_runs)

        self.assertEqual(closed, {"200"})

        delete_calls = [
            c for c in cursor.execute.call_args_list
            if "DELETE FROM tbl_active_executions" in c.args[0]
        ]
        self.assertEqual(len(delete_calls), 1)
        # workspace_id vai primeiro, "200" é o único run_id apagado.
        self.assertEqual(delete_calls[0].args[1], "ws1")
        self.assertIn("200", delete_calls[0].args[2:])
        self.assertNotIn("100", delete_calls[0].args[2:])

    def test_removal_never_touches_another_workspace(self):
        # Os dois workspaces compartilham o mesmo run_id "100" — coincidência
        # possível, já que run_id só é único por workspace (§4.2). Fechar
        # "100" em ws1 não pode gerar um DELETE que também alcance ws2.
        conn = _conn_with_active({"ws1": {"100"}, "ws2": {"100"}})
        cursor = conn.cursor.return_value

        closed_ws1 = dbx.reconcile_active_executions(conn, "ws1", current_runs={})
        self.assertEqual(closed_ws1, {"100"})

        delete_calls = [
            c for c in cursor.execute.call_args_list
            if "DELETE FROM tbl_active_executions" in c.args[0]
        ]
        self.assertEqual(len(delete_calls), 1)
        self.assertEqual(delete_calls[0].args[1], "ws1")
        self.assertNotIn("ws2", delete_calls[0].args)

    def test_new_run_is_upserted(self):
        conn = _conn_with_active({"ws1": set()})
        cursor = conn.cursor.return_value

        current_runs = {
            "300": {"job_id": "9", "run_name": "novo",
                    "state": {"life_cycle_state": "RUNNING"},
                    "start_time": 1_700_000_000_000}
        }
        closed = dbx.reconcile_active_executions(conn, "ws1", current_runs)

        self.assertEqual(closed, set())
        upsert_calls = [
            c for c in cursor.execute.call_args_list
            if "MERGE tbl_active_executions" in c.args[0]
        ]
        self.assertEqual(len(upsert_calls), 1)
        self.assertEqual(upsert_calls[0].args[1], "ws1")
        self.assertEqual(upsert_calls[0].args[2], "300")

    def test_empty_response_closes_everything_for_that_workspace(self):
        conn = _conn_with_active({"ws1": {"100", "200"}})
        closed = dbx.reconcile_active_executions(conn, "ws1", current_runs={})
        self.assertEqual(closed, {"100", "200"})

    def test_commits_once(self):
        conn = _conn_with_active({"ws1": set()})
        dbx.reconcile_active_executions(conn, "ws1", current_runs={})
        conn.commit.assert_called_once()


class TestRecoveryStartTime(unittest.TestCase):
    """start_time_from do passo (c): min(watermark, piso do passo b) menos
    a sobreposição. Errar isso em qualquer direção reabre o defeito que os
    três mecanismos existem para evitar: perder um run longo, ou nunca
    avançar a janela."""

    def test_no_open_execution_uses_watermark_alone(self):
        watermark = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        result = dbx.recovery_start_time(watermark, None, overlap_minutes=15)
        self.assertEqual(result, watermark - timedelta(minutes=15))

    def test_older_open_execution_pulls_the_floor_back(self):
        # Um job que começou 3 horas atrás e segue aberto: a janela do passo
        # (c) precisa alcançar esse horário, não só os últimos 15 minutos —
        # senão a varredura por start_time nunca o alcança quando ele fechar.
        watermark = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        floor = datetime(2024, 6, 1, 9, 0, tzinfo=timezone.utc)
        result = dbx.recovery_start_time(watermark, floor, overlap_minutes=15)
        self.assertEqual(result, floor - timedelta(minutes=15))

    def test_newer_open_execution_never_advances_past_watermark(self):
        # Um lookback fixo arbitrário não faria essa distinção; aqui o
        # menor dos dois é que manda, nunca o piso do passo (b) sozinho.
        watermark = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        floor = datetime(2024, 6, 1, 13, 0, tzinfo=timezone.utc)
        result = dbx.recovery_start_time(watermark, floor, overlap_minutes=15)
        self.assertEqual(result, watermark - timedelta(minutes=15))


class TestFloorStartedAt(unittest.TestCase):
    def test_no_current_runs_returns_none(self):
        self.assertIsNone(dbx._floor_started_at({}))

    def test_picks_the_oldest_start_time(self):
        runs = {
            "1": {"start_time": 1_700_000_000_000},
            "2": {"start_time": 1_699_000_000_000},  # mais antigo
        }
        floor = dbx._floor_started_at(runs)
        self.assertEqual(floor, dbx._epoch_ms_to_datetime(1_699_000_000_000))

    def test_ignores_runs_without_start_time(self):
        runs = {"1": {}, "2": {"start_time": 1_700_000_000_000}}
        floor = dbx._floor_started_at(runs)
        self.assertEqual(floor, dbx._epoch_ms_to_datetime(1_700_000_000_000))


class TestEpochMsToDatetime(unittest.TestCase):
    def test_none_stays_none(self):
        self.assertIsNone(dbx._epoch_ms_to_datetime(None))

    def test_zero_stays_none(self):
        # 0 não é "meia-noite de 1970", é "a API não preencheu o campo".
        self.assertIsNone(dbx._epoch_ms_to_datetime(0))

    def test_converts_to_utc(self):
        result = dbx._epoch_ms_to_datetime(1_700_000_000_000)
        self.assertEqual(result.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
