from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from bot.utils.time import (
    DateParseError,
    from_db,
    humanize_minutes,
    parse_user_datetime,
    to_db,
)

TZ = ZoneInfo("Europe/Paris")
# Lundi 28 septembre 2026, 12:00 à Paris
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)


def local(raw: str) -> datetime:
    return parse_user_datetime(raw, TZ, now=NOW).astimezone(TZ)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("28/09/2026 21:00", (2026, 9, 28, 21, 0)),
        ("28/09 21h", (2026, 9, 28, 21, 0)),
        ("28-09-26 21h15", (2026, 9, 28, 21, 15)),
        ("2026-10-01 20:00", (2026, 10, 1, 20, 0)),
        ("demain 21h", (2026, 9, 29, 21, 0)),
        ("aujourd'hui 20h30", (2026, 9, 28, 20, 30)),
        ("après-demain 19h", (2026, 9, 30, 19, 0)),
        ("samedi 18h", (2026, 10, 3, 18, 0)),
        ("lundi 18h", (2026, 10, 5, 18, 0)),  # « lundi » = lundi prochain
        ("samedi à 18h", (2026, 10, 3, 18, 0)),
        ("21h", (2026, 9, 28, 21, 0)),
        ("11h", (2026, 9, 29, 11, 0)),  # déjà passée aujourd'hui => demain
    ],
)
def test_parse_valid(raw, expected):
    dt = local(raw)
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == expected


def test_day_month_in_past_rolls_to_next_year():
    assert local("05/01 20h").year == 2027


@pytest.mark.parametrize("raw", ["", "n'importe quoi", "32/01 20h", "demain 25h", "28/09 21h99"])
def test_parse_invalid(raw):
    with pytest.raises(DateParseError):
        parse_user_datetime(raw, TZ, now=NOW)


def test_result_is_utc():
    assert parse_user_datetime("28/09/2026 21:00", TZ, now=NOW) == datetime(2026, 9, 28, 19, 0, tzinfo=timezone.utc)


def test_db_roundtrip():
    dt = datetime(2026, 9, 28, 19, 0, 30, 123456, tzinfo=timezone.utc)
    assert from_db(to_db(dt)) == dt.replace(microsecond=0)
    assert from_db(None) is None


def test_humanize():
    assert humanize_minutes(1440) == "1 jour"
    assert humanize_minutes(120) == "2 heures"
    assert humanize_minutes(15) == "15 minutes"
