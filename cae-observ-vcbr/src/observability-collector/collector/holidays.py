"""Feriados nacionais do Brasil — usado só por `expectations.py` (§4.4),
para expectativas com `observes_national_holidays = False`.

Cobre só os feriados **federais** fixados em lei, os únicos que valem no
país inteiro sem depender de decreto municipal/estadual:

    Confraternização Universal    01/01
    Sexta-feira Santa             móvel (Páscoa - 2 dias)
    Tiradentes                    21/04
    Dia do Trabalho                01/05
    Independência                  07/09
    Nossa Senhora Aparecida        12/10
    Finados                        02/11
    Proclamação da República       15/11
    Consciência Negra              20/11 (federal desde a Lei 14.759/2023)
    Natal                          25/12

De propósito fora: Carnaval e Corpo de Cristo são ponto facultativo por
decreto, não feriado nacional por lei — um job que realmente pausa nessas
datas entra como caso `MANUAL`/`YAML`, não aqui. Feriado municipal/estadual
(ex.: aniversário de cidade) nunca entra nesta função — é sinal de escopo
único (VCBR), não de "feriado nacional".
"""

from __future__ import annotations

from datetime import date, timedelta


def _easter_sunday(year: int) -> date:
    """Domingo de Páscoa, algoritmo de Meeus/Jones/Butcher (calendário
    gregoriano) — mesmo algoritmo usado por qualquer biblioteca de feriados,
    reproduzido aqui para não trazer dependência externa por 8 linhas."""
    a = year % 19
    b = year // 100
    c_ = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c_ // 4
    k = c_ % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _fixed_holidays(year: int) -> set[date]:
    return {
        date(year, 1, 1),    # Confraternização Universal
        date(year, 4, 21),   # Tiradentes
        date(year, 5, 1),    # Dia do Trabalho
        date(year, 9, 7),    # Independência
        date(year, 10, 12),  # Nossa Senhora Aparecida
        date(year, 11, 2),   # Finados
        date(year, 11, 15),  # Proclamação da República
        date(year, 11, 20),  # Consciência Negra (federal desde 2023)
        date(year, 12, 25),  # Natal
    }


def _movable_holidays(year: int) -> set[date]:
    return {_easter_sunday(year) - timedelta(days=2)}  # Sexta-feira Santa


def is_national_holiday(d: date) -> bool:
    """True se `d` é feriado nacional federal no Brasil."""
    return d in _fixed_holidays(d.year) | _movable_holidays(d.year)
