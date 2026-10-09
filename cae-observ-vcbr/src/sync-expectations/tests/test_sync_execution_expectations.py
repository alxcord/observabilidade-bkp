"""Testes do sincronizador de tbl_cfg_execution_expectations.

O módulo sob teste importa azure-identity, httpx e pyodbc no topo. Aqui eles
são substituídos por stubs antes da importação, para que a suíte rode em
qualquer máquina e no agente de CI sem driver ODBC instalado — o que importa
testar é a lógica de conversão e de decisão, não a biblioteca de terceiros.
"""

from __future__ import annotations

import sys
import types
import unittest
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sync_execution_expectations as sync  # noqa: E402


class TestQuartzToSchedule(unittest.TestCase):
    """Normalização do cron Quartz do Databricks — nunca lido pelo avaliador.

    Uma expectativa errada gera alerta de ausência falso, que destrói a
    confiança no canal mais rápido do que alerta ausente. Por isso os casos
    que não normalizam com segurança precisam devolver None, testado com o
    mesmo rigor dos que normalizam.
    """

    def test_minute_interval(self):
        self.assertEqual(
            sync._quartz_to_schedule("0 */15 * * * ?"),
            {"schedule_kind": "INTERVAL", "interval_unit": "MINUTE",
             "interval_value": 15, "time_of_day": None, "days_of_week": None,
             "day_of_month": None, "last_day_of_month": False},
        )

    def test_hour_interval_with_fixed_minute(self):
        self.assertEqual(
            sync._quartz_to_schedule("0 30 */4 * * ?"),
            {"schedule_kind": "INTERVAL", "interval_unit": "HOUR",
             "interval_value": 4, "time_of_day": None, "days_of_week": None,
             "day_of_month": None, "last_day_of_month": False},
        )

    def test_daily_at_fixed_time(self):
        self.assertEqual(
            sync._quartz_to_schedule("0 0 6 * * ?"),
            {"schedule_kind": "TIME_OF_DAY", "interval_unit": None,
             "interval_value": None, "time_of_day": "06:00:00",
             "days_of_week": None, "day_of_month": None,
             "last_day_of_month": False},
        )

    def test_weekly_on_specific_days(self):
        self.assertEqual(
            sync._quartz_to_schedule("0 15 7 ? * MON,WED,FRI"),
            {"schedule_kind": "TIME_OF_DAY", "interval_unit": None,
             "interval_value": None, "time_of_day": "07:15:00",
             "days_of_week": "MON,WED,FRI", "day_of_month": None,
             "last_day_of_month": False},
        )

    def test_monthly_on_fixed_day(self):
        self.assertEqual(
            sync._quartz_to_schedule("0 0 3 1 * ?"),
            {"schedule_kind": "MONTHLY", "interval_unit": None,
             "interval_value": None, "time_of_day": "03:00:00",
             "days_of_week": None, "day_of_month": 1,
             "last_day_of_month": False},
        )

    def test_monthly_last_day(self):
        self.assertEqual(
            sync._quartz_to_schedule("0 0 22 L * ?"),
            {"schedule_kind": "MONTHLY", "interval_unit": None,
             "interval_value": None, "time_of_day": "22:00:00",
             "days_of_week": None, "day_of_month": None,
             "last_day_of_month": True},
        )

    def test_nonzero_seconds_does_not_normalize(self):
        self.assertIsNone(sync._quartz_to_schedule("30 0 6 * * ?"))

    def test_multiple_hours_does_not_normalize(self):
        self.assertIsNone(sync._quartz_to_schedule("0 0 6,18 * * ?"))

    def test_nth_weekday_does_not_normalize(self):
        self.assertIsNone(sync._quartz_to_schedule("0 0 6 ? * MON#1"))

    def test_malformed_expression_does_not_normalize(self):
        self.assertIsNone(sync._quartz_to_schedule("not a cron"))


class TestRecurrenceToSchedule(unittest.TestCase):
    """Normalização da recorrência do ADF — já estruturada, sem cron."""

    def test_daily_at_fixed_time(self):
        r = {"frequency": "Day", "interval": 1,
             "schedule": {"hours": [6], "minutes": [0]}}
        self.assertEqual(
            sync._recurrence_to_schedule(r),
            {"schedule_kind": "TIME_OF_DAY", "interval_unit": None,
             "interval_value": None, "time_of_day": "06:00:00",
             "days_of_week": None, "day_of_month": None,
             "last_day_of_month": False},
        )

    def test_hourly_interval(self):
        r = {"frequency": "Hour", "interval": 4, "schedule": {}}
        self.assertEqual(
            sync._recurrence_to_schedule(r),
            {"schedule_kind": "INTERVAL", "interval_unit": "HOUR",
             "interval_value": 4, "time_of_day": None, "days_of_week": None,
             "day_of_month": None, "last_day_of_month": False},
        )

    def test_weekly_on_specific_days(self):
        r = {"frequency": "Week", "interval": 1,
             "schedule": {"hours": [7], "minutes": [15],
                          "weekDays": ["Monday", "Wednesday"]}}
        self.assertEqual(
            sync._recurrence_to_schedule(r),
            {"schedule_kind": "TIME_OF_DAY", "interval_unit": None,
             "interval_value": None, "time_of_day": "07:15:00",
             "days_of_week": "MON,WED", "day_of_month": None,
             "last_day_of_month": False},
        )

    def test_multiple_times_per_day_does_not_normalize(self):
        r = {"frequency": "Day", "interval": 1,
             "schedule": {"hours": [6, 18], "minutes": [0]}}
        self.assertIsNone(sync._recurrence_to_schedule(r))

    def test_no_schedule_does_not_normalize(self):
        self.assertIsNone(sync._recurrence_to_schedule(
            {"frequency": "Day", "interval": 1}))

    def test_invalid_weekday_does_not_normalize(self):
        r = {"frequency": "Week", "interval": 1,
             "schedule": {"hours": [7], "minutes": [0], "weekDays": ["Feriado"]}}
        self.assertIsNone(sync._recurrence_to_schedule(r))

    def test_unknown_frequency_does_not_normalize(self):
        r = {"frequency": "Minute", "interval": 5, "schedule": {"minutes": [0]}}
        self.assertIsNone(sync._recurrence_to_schedule(r))

    def test_day_interval_above_one_does_not_normalize(self):
        # "a cada N dias" não tem representação em TIME_OF_DAY (que é
        # diário ou semanal por dia-da-semana, não por intervalo de dias).
        r = {"frequency": "Day", "interval": 2,
             "schedule": {"hours": [6], "minutes": [0]}}
        self.assertIsNone(sync._recurrence_to_schedule(r))


class TestTimeoutToMinutes(unittest.TestCase):
    """Duração máxima esperada, derivada do timeout declarado do job."""

    def test_converts_seconds_to_minutes(self):
        self.assertEqual(sync._timeout_to_minutes({"timeout_seconds": 3600}), 60)

    def test_no_timeout_returns_none(self):
        # Sem timeout declarado, a deteccao de job travado simplesmente nao se
        # aplica aquele job — preferivel a inventar um limite e gerar falso positivo.
        self.assertIsNone(sync._timeout_to_minutes({}))
        self.assertIsNone(sync._timeout_to_minutes({"timeout_seconds": 0}))

    def test_very_short_timeout_becomes_one_minute(self):
        self.assertEqual(sync._timeout_to_minutes({"timeout_seconds": 30}), 1)


def _make_expectation(**overrides) -> "sync.Expectation":
    defaults = dict(
        source="DATABRICKS", natural_key="123", description="job",
        schedule_kind="TIME_OF_DAY", interval_unit=None, interval_value=None,
        time_of_day="06:00:00", days_of_week=None, day_of_month=None,
        last_day_of_month=False, observes_national_holidays=True,
        timezone="America/Sao_Paulo", tolerance_minutes=15,
        max_expected_duration_min=60,
        written_by=sync.WRITTEN_BY_DATABRICKS, source_expression="0 0 6 * * ?",
    )
    defaults.update(overrides)
    return sync.Expectation(**defaults)


class TestPersist(unittest.TestCase):
    """Guarda contra apagar configuração por falha de leitura."""

    def test_empty_set_changes_nothing(self):
        # Nenhuma expectativa devolvida quase sempre significa falha de leitura,
        # nao que a empresa parou de agendar jobs. Desativar tudo aqui apagaria
        # a configuracao inteira por causa de um timeout de API.
        conn = MagicMock()
        sync.persist(conn, [], "DATABRICKS")
        conn.cursor.assert_not_called()
        conn.commit.assert_not_called()

    def test_set_with_items_upserts_and_commits(self):
        conn = MagicMock()
        sync.persist(conn, [_make_expectation()], "DATABRICKS")
        self.assertTrue(conn.cursor.return_value.execute.called)
        conn.commit.assert_called_once()

    def test_config_source_travels_to_every_upsert_call(self):
        # A guarda "AUTO nunca sobrescreve MANUAL" depende de config_source
        # ir em todo INSERT/UPDATE, não só no filtro.
        conn = MagicMock()
        sync.persist(conn, [_make_expectation()], "DATABRICKS", "MANUAL")
        upsert_call = conn.cursor.return_value.execute.call_args_list[0]
        self.assertIn("MANUAL", upsert_call.args)


if __name__ == "__main__":
    unittest.main()
