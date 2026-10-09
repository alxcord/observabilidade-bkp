"""Testes do avaliador de ausência de execução e job travado (§4.4).

pyodbc é substituído por um stub antes da importação, mesmo padrão de
test_resolution.py — a lógica de abrir/incrementar incidente não é testada
aqui (é `src/shared/tests/test_event_resolution.py`); o que importa testar
é a aritmética de data (schedule_kind -> última ocorrência esperada) e a
decisão de quando chamar resolve_event.
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import date, datetime, time, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


def _stub(name: str, **attrs: object) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


_stub("pyodbc", connect=MagicMock(), Connection=object, IntegrityError=Exception)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

import expectations  # noqa: E402


def _exp(**overrides) -> expectations.Expectation:
    defaults = dict(
        source="DATABRICKS", natural_key="ws1:123", schedule_kind="TIME_OF_DAY",
        interval_unit=None, interval_value=None, time_of_day=time(6, 0, 0),
        days_of_week=None, day_of_month=None, last_day_of_month=False,
        observes_national_holidays=True, timezone="America/Sao_Paulo",
        tolerance_minutes=15,
    )
    defaults.update(overrides)
    return expectations.Expectation(**defaults)


class TestOccursOn(unittest.TestCase):
    def test_time_of_day_without_weekday_filter_occurs_every_day(self):
        exp = _exp(days_of_week=None)
        self.assertTrue(expectations._occurs_on(exp, date(2026, 3, 10)))

    def test_time_of_day_with_weekday_filter(self):
        exp = _exp(days_of_week="MON,WED,FRI")
        self.assertTrue(expectations._occurs_on(exp, date(2026, 3, 9)))   # segunda
        self.assertFalse(expectations._occurs_on(exp, date(2026, 3, 10)))  # terça

    def test_monthly_on_fixed_day(self):
        exp = _exp(schedule_kind="MONTHLY", day_of_month=15)
        self.assertTrue(expectations._occurs_on(exp, date(2026, 3, 15)))
        self.assertFalse(expectations._occurs_on(exp, date(2026, 3, 16)))

    def test_monthly_last_day(self):
        exp = _exp(schedule_kind="MONTHLY", last_day_of_month=True)
        self.assertTrue(expectations._occurs_on(exp, date(2026, 2, 28)))
        self.assertFalse(expectations._occurs_on(exp, date(2026, 2, 27)))
        # Ano bissexto: fevereiro de 2028 tem 29 dias.
        self.assertTrue(expectations._occurs_on(exp, date(2028, 2, 29)))

    def test_national_holiday_excluded_when_not_observed(self):
        exp = _exp(observes_national_holidays=False)
        self.assertFalse(expectations._occurs_on(exp, date(2026, 1, 1)))

    def test_national_holiday_still_occurs_when_observed(self):
        exp = _exp(observes_national_holidays=True)
        self.assertTrue(expectations._occurs_on(exp, date(2026, 1, 1)))


class TestLastDue(unittest.TestCase):
    def test_daily_before_time_of_day_returns_yesterday(self):
        exp = _exp(time_of_day=time(6, 0, 0))
        now_local = datetime(2026, 3, 10, 5, 0, 0, tzinfo=timezone.utc)
        due = expectations._last_due(exp, now_local)
        self.assertEqual(due.date(), date(2026, 3, 9))

    def test_daily_after_time_of_day_returns_today(self):
        exp = _exp(time_of_day=time(6, 0, 0))
        now_local = datetime(2026, 3, 10, 7, 0, 0, tzinfo=timezone.utc)
        due = expectations._last_due(exp, now_local)
        self.assertEqual(due.date(), date(2026, 3, 10))

    def test_weekly_skips_to_matching_weekday(self):
        # 2026-03-10 é terça; só segunda/quarta/sexta valem.
        exp = _exp(time_of_day=time(6, 0, 0), days_of_week="MON,WED,FRI")
        now_local = datetime(2026, 3, 10, 7, 0, 0, tzinfo=timezone.utc)
        due = expectations._last_due(exp, now_local)
        self.assertEqual(due.date(), date(2026, 3, 9))  # segunda anterior

    def test_monthly_before_day_falls_back_to_previous_month(self):
        exp = _exp(schedule_kind="MONTHLY", day_of_month=1, time_of_day=time(3, 0, 0))
        now_local = datetime(2026, 3, 1, 2, 0, 0, tzinfo=timezone.utc)  # antes das 3h
        due = expectations._last_due(exp, now_local)
        self.assertEqual(due.date(), date(2026, 2, 1))

    def test_no_matching_day_returns_none(self):
        # day_of_month=31 nunca ocorre em fevereiro nem abril; ainda assim
        # bate em algum mês de 31 dias dentro da janela de busca, então
        # força a ausência total usando um filtro de dia da semana
        # impossível para provar o caminho "nada encontrado".
        exp = _exp(days_of_week="NAO_EXISTE")
        now_local = datetime(2026, 3, 10, 7, 0, 0, tzinfo=timezone.utc)
        self.assertIsNone(expectations._last_due(exp, now_local))


class TestEvaluateAbsence(unittest.TestCase):
    def test_interval_without_prior_observation_is_skipped(self):
        exp = _exp(schedule_kind="INTERVAL", interval_unit="MINUTE", interval_value=15)
        conn = MagicMock()
        with patch.object(expectations, "_last_observed", return_value=None), \
             patch.object(expectations.resolution, "resolve_event") as resolve:
            opened = expectations.evaluate_absence(conn, [exp])
        resolve.assert_not_called()
        self.assertEqual(opened, 0)

    def test_interval_past_deadline_opens_incident(self):
        exp = _exp(schedule_kind="INTERVAL", interval_unit="MINUTE",
                    interval_value=15, tolerance_minutes=5)
        last_observed = datetime(2026, 3, 10, 6, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 3, 10, 6, 25, 0, tzinfo=timezone.utc)  # 25 min depois
        conn = MagicMock()
        with patch.object(expectations, "_last_observed", return_value=last_observed), \
             patch.object(expectations.resolution, "resolve_event") as resolve:
            opened = expectations.evaluate_absence(conn, [exp], now=now)
        resolve.assert_called_once()
        kwargs = resolve.call_args.kwargs
        self.assertEqual(kwargs["error_code"], "JOB_NOT_EXECUTED")
        self.assertEqual(kwargs["natural_key"], "ws1:123")
        self.assertEqual(opened, 1)

    def test_interval_within_tolerance_is_not_opened(self):
        exp = _exp(schedule_kind="INTERVAL", interval_unit="MINUTE",
                    interval_value=15, tolerance_minutes=5)
        last_observed = datetime(2026, 3, 10, 6, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 3, 10, 6, 10, 0, tzinfo=timezone.utc)  # dentro de 15+5
        conn = MagicMock()
        with patch.object(expectations, "_last_observed", return_value=last_observed), \
             patch.object(expectations.resolution, "resolve_event") as resolve:
            expectations.evaluate_absence(conn, [exp], now=now)
        resolve.assert_not_called()

    def test_time_of_day_missing_opens_incident(self):
        exp = _exp(time_of_day=time(6, 0, 0), tolerance_minutes=15,
                    timezone="UTC")
        now = datetime(2026, 3, 10, 6, 30, 0, tzinfo=timezone.utc)  # 30 min depois
        conn = MagicMock()
        with patch.object(expectations, "_last_observed", return_value=None), \
             patch.object(expectations.resolution, "resolve_event") as resolve:
            opened = expectations.evaluate_absence(conn, [exp], now=now)
        resolve.assert_called_once()
        self.assertEqual(opened, 1)

    def test_time_of_day_observed_after_due_is_satisfied(self):
        exp = _exp(time_of_day=time(6, 0, 0), tolerance_minutes=15,
                    timezone="UTC")
        now = datetime(2026, 3, 10, 6, 30, 0, tzinfo=timezone.utc)
        observed_after_due = datetime(2026, 3, 10, 6, 5, 0, tzinfo=timezone.utc)
        conn = MagicMock()
        with patch.object(expectations, "_last_observed",
                           return_value=observed_after_due), \
             patch.object(expectations.resolution, "resolve_event") as resolve:
            expectations.evaluate_absence(conn, [exp], now=now)
        resolve.assert_not_called()

    def test_time_of_day_not_yet_due_is_not_opened(self):
        exp = _exp(time_of_day=time(6, 0, 0), tolerance_minutes=15,
                    timezone="UTC")
        now = datetime(2026, 3, 10, 6, 5, 0, tzinfo=timezone.utc)  # dentro da tolerância
        conn = MagicMock()
        with patch.object(expectations, "_last_observed", return_value=None), \
             patch.object(expectations.resolution, "resolve_event") as resolve:
            expectations.evaluate_absence(conn, [exp], now=now)
        resolve.assert_not_called()

    def test_invalid_timezone_is_skipped_without_raising(self):
        exp = _exp(timezone="Not/A_Zone")
        conn = MagicMock()
        with patch.object(expectations.resolution, "resolve_event") as resolve:
            opened = expectations.evaluate_absence(conn, [exp])
        resolve.assert_not_called()
        self.assertEqual(opened, 0)


class _StuckRow:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class TestEvaluateStuck(unittest.TestCase):
    def test_no_candidates_opens_nothing(self):
        conn = MagicMock()
        conn.cursor.return_value.execute.return_value.fetchall.return_value = []
        with patch.object(expectations.resolution, "resolve_event") as resolve:
            opened = expectations.evaluate_stuck(conn)
        resolve.assert_not_called()
        self.assertEqual(opened, 0)

    def test_stuck_candidate_opens_incident(self):
        conn = MagicMock()
        row = _StuckRow(
            workspace_id="ws1", job_id="123", run_id="999",
            run_name="job x", started_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
            max_expected_duration_min=60,
        )
        conn.cursor.return_value.execute.return_value.fetchall.return_value = [row]
        with patch.object(expectations.resolution, "resolve_event") as resolve:
            opened = expectations.evaluate_stuck(conn)
        resolve.assert_called_once()
        kwargs = resolve.call_args.kwargs
        self.assertEqual(kwargs["error_code"], "JOB_STUCK")
        self.assertEqual(kwargs["natural_key"], "ws1:123")
        self.assertEqual(opened, 1)


if __name__ == "__main__":
    unittest.main()
