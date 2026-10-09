"""Testes de holidays.py — feriados nacionais usados pelo avaliador de
ausência (§4.4) quando observes_national_holidays = False."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

import holidays  # noqa: E402


class TestFixedHolidays(unittest.TestCase):
    def test_known_fixed_dates(self):
        self.assertTrue(holidays.is_national_holiday(date(2026, 1, 1)))
        self.assertTrue(holidays.is_national_holiday(date(2026, 4, 21)))
        self.assertTrue(holidays.is_national_holiday(date(2026, 5, 1)))
        self.assertTrue(holidays.is_national_holiday(date(2026, 9, 7)))
        self.assertTrue(holidays.is_national_holiday(date(2026, 10, 12)))
        self.assertTrue(holidays.is_national_holiday(date(2026, 11, 2)))
        self.assertTrue(holidays.is_national_holiday(date(2026, 11, 15)))
        self.assertTrue(holidays.is_national_holiday(date(2026, 11, 20)))
        self.assertTrue(holidays.is_national_holiday(date(2026, 12, 25)))

    def test_ordinary_day_is_not_holiday(self):
        self.assertFalse(holidays.is_national_holiday(date(2026, 3, 10)))


class TestMovableHoliday(unittest.TestCase):
    def test_good_friday_2026(self):
        # Páscoa 2026 cai em 5 de abril; Sexta-feira Santa é dois dias antes.
        self.assertTrue(holidays.is_national_holiday(date(2026, 4, 3)))

    def test_good_friday_2024(self):
        # Páscoa 2024: 31 de março; Sexta-feira Santa: 29 de março.
        self.assertTrue(holidays.is_national_holiday(date(2024, 3, 29)))

    def test_day_before_good_friday_is_not_holiday(self):
        self.assertFalse(holidays.is_national_holiday(date(2026, 4, 2)))


class TestCarnavalIsExcludedByDesign(unittest.TestCase):
    def test_carnaval_tuesday_is_not_a_national_holiday(self):
        # Carnaval 2026: terça-feira 17/02 — ponto facultativo por decreto,
        # não feriado nacional por lei. Ver docstring do módulo.
        self.assertFalse(holidays.is_national_holiday(date(2026, 2, 17)))


if __name__ == "__main__":
    unittest.main()
