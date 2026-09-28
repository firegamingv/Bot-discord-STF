"""Tests du parsing des délais de rappel (``/config rappels``)."""

from __future__ import annotations

import pytest

from bot.features.admin.reminder_parser import (
    MAX_MINUTES,
    ReminderParseError,
    parse_duration,
    parse_reminder_offsets,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1j", 1440),
        ("2 jours", 2880),
        ("1h", 60),
        ("3 heures", 180),
        ("15min", 15),
        ("15 min", 15),
        ("10m", 10),
        ("45", 45),
        ("1h30", 90),
        ("1j12", 1440 + 12 * 60),
        ("1d", 1440),
        ("2 jours 3 heures", 2 * 1440 + 180),
        ("1H", 60),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "1x", "h1", "1h-2", "1 h 30 zz"])
def test_parse_duration_invalid(text):
    with pytest.raises(ReminderParseError):
        parse_duration(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1j, 1h, 15min", [1440, 60, 15]),
        ("15min, 1j, 1h", [1440, 60, 15]),  # trié du plus long au plus court
        ("1j; 1h / 15 min", [1440, 60, 15]),
        ("1j et 1h", [1440, 60]),
        ("1j 1h 15min", [1440, 60, 15]),  # espaces seuls entre délais complets
        ("1h 30", [90]),  # mais "1h 30" = 1h30
        ("1h, 60min", [60]),  # doublons fusionnés
        ("7j", [MAX_MINUTES]),
        ("1min", [1]),
        ("aucun", []),
        ("  Aucun ", []),
        ("off", []),
    ],
)
def test_parse_reminder_offsets(text, expected):
    assert parse_reminder_offsets(text) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("8j", "trop long"),
        ("0min", "trop court"),
        ("1j, 2j, 3j, 4j, 5j, 6j", "5 maximum"),
        ("1 semaine", "Unité inconnue"),
        (",,", "Aucun délai"),
        ("demain", "illisible"),
    ],
)
def test_parse_reminder_offsets_errors(text, message):
    with pytest.raises(ReminderParseError, match=message):
        parse_reminder_offsets(text)
