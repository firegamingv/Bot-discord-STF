from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bot.services.periods import day_bounds, is_due, last_occurrence, period_bounds

PARIS = ZoneInfo("Europe/Paris")
UTC = timezone.utc


def test_day_bounds_local_midnight():
    now = datetime(2026, 9, 28, 23, 30, tzinfo=UTC)  # 29/09 01:30 à Paris
    start, end = day_bounds(PARIS, now)
    assert start == datetime(2026, 9, 28, 22, 0, tzinfo=UTC)
    assert end == datetime(2026, 9, 29, 22, 0, tzinfo=UTC)
    tomorrow = day_bounds(PARIS, now, offset_days=1)
    assert tomorrow[0] == end


def test_week_starts_monday():
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)  # jeudi
    start, end = period_bounds("week", PARIS, now)
    assert start.astimezone(PARIS).weekday() == 0
    assert start.astimezone(PARIS).date().isoformat() == "2026-09-28"
    assert end - start == timedelta(days=7)


def test_month_and_december():
    start, end = period_bounds("month", PARIS, datetime(2026, 12, 15, tzinfo=UTC))
    assert start.astimezone(PARIS).date().isoformat() == "2026-12-01"
    assert end.astimezone(PARIS).date().isoformat() == "2027-01-01"


def test_month_dst_change():
    # octobre : passage à l'heure d'hiver le 25/10/2026
    start, end = period_bounds("month", PARIS, datetime(2026, 10, 10, tzinfo=UTC))
    assert start == datetime(2026, 9, 30, 22, tzinfo=UTC)
    assert end == datetime(2026, 10, 31, 23, tzinfo=UTC)


def test_all_and_unknown():
    assert period_bounds("all", PARIS) == (None, None)
    with pytest.raises(ValueError):
        period_bounds("year", PARIS)


def test_last_occurrence_and_due():
    now = datetime(2026, 9, 30, 18, 30, tzinfo=UTC)  # mercredi 20:30 Paris
    occ = last_occurrence("daily", weekday=0, hour=20, tz=PARIS, now=now)
    assert occ == datetime(2026, 9, 30, 18, 0, tzinfo=UTC)
    weekly = last_occurrence("weekly", weekday=0, hour=20, tz=PARIS, now=now)
    assert weekly.astimezone(PARIS).weekday() == 0 and weekly < now
    assert is_due("daily", weekday=0, hour=20, tz=PARIS, last_posted_at=None, now=now)
    assert not is_due("daily", weekday=0, hour=20, tz=PARIS, last_posted_at=now, now=now)
    # Échéance trop ancienne (> 6 h) : on saute
    assert not is_due("weekly", weekday=0, hour=20, tz=PARIS, last_posted_at=None, now=now)
