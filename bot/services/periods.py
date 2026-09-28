"""Périodes de classement et d'horaires, calculées en heure locale et renvoyées en UTC.

- ``day``   : aujourd'hui (minuit local → minuit local suivant)
- ``week``  : semaine en cours, du lundi 00:00 local au lundi suivant
- ``month`` : mois en cours
- ``all``   : depuis toujours (bornes ``None``)
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc

PERIOD_DAY = "day"
PERIOD_WEEK = "week"
PERIOD_MONTH = "month"
PERIOD_ALL = "all"
PERIODS = (PERIOD_DAY, PERIOD_WEEK, PERIOD_MONTH, PERIOD_ALL)
PERIOD_LABELS = {
    PERIOD_DAY: "Aujourd'hui",
    PERIOD_WEEK: "Cette semaine",
    PERIOD_MONTH: "Ce mois-ci",
    PERIOD_ALL: "Depuis toujours",
}

WEEKDAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
WEEKDAYS_FR_SHORT = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC)


def local_today(tz: ZoneInfo, now: datetime | None = None) -> date:
    return _now(now).astimezone(tz).date()


def _local_midnight(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=tz).astimezone(UTC)


def day_bounds(tz: ZoneInfo, now: datetime | None = None, *, offset_days: int = 0) -> tuple[datetime, datetime]:
    """Début et fin (exclue) d'une journée locale, en UTC."""
    d = local_today(tz, now) + timedelta(days=offset_days)
    return _local_midnight(d, tz), _local_midnight(d + timedelta(days=1), tz)


def period_bounds(
    period: str, tz: ZoneInfo, now: datetime | None = None
) -> tuple[datetime | None, datetime | None]:
    """Bornes UTC ``[début, fin[`` de la période en cours ; ``(None, None)`` pour ``all``."""
    today = local_today(tz, now)
    if period == PERIOD_DAY:
        start = today
        end = today + timedelta(days=1)
    elif period == PERIOD_WEEK:
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
    elif period == PERIOD_MONTH:
        start = today.replace(day=1)
        end = (start.replace(year=start.year + 1, month=1) if start.month == 12
               else start.replace(month=start.month + 1))
    elif period == PERIOD_ALL:
        return None, None
    else:
        raise ValueError(f"Période inconnue : {period!r}")
    return _local_midnight(start, tz), _local_midnight(end, tz)


def period_label(period: str) -> str:
    return PERIOD_LABELS.get(period, period)


def last_occurrence(
    frequency: str, *, weekday: int, hour: int, tz: ZoneInfo, now: datetime | None = None
) -> datetime:
    """Dernière échéance (UTC) ≤ maintenant d'un horaire local quotidien ou hebdomadaire.

    ``frequency`` : ``daily`` (tous les jours à ``hour``) ou ``weekly`` (le ``weekday`` à ``hour``,
    0 = lundi).
    """
    local_now = _now(now).astimezone(tz)
    candidate_day = local_now.date()
    if frequency == "weekly":
        candidate_day -= timedelta(days=(candidate_day.weekday() - weekday) % 7)
    candidate = datetime.combine(candidate_day, time(hour, 0), tzinfo=tz)
    if candidate > local_now:
        candidate -= timedelta(days=7 if frequency == "weekly" else 1)
        # Recalage éventuel après un changement d'heure
        candidate = datetime.combine(candidate.date(), time(hour, 0), tzinfo=tz)
    return candidate.astimezone(UTC)


def is_due(
    frequency: str,
    *,
    weekday: int,
    hour: int,
    tz: ZoneInfo,
    last_posted_at: datetime | None,
    now: datetime | None = None,
    grace: timedelta = timedelta(hours=6),
) -> bool:
    """Vrai si une publication programmée doit partir maintenant.

    On publie si la dernière échéance n'a pas encore été servie et qu'elle date de moins de
    ``grace`` (évite de publier un vieux classement après une longue panne).
    """
    occurrence = last_occurrence(frequency, weekday=weekday, hour=hour, tz=tz, now=now)
    if _now(now) - occurrence > grace:
        return False
    return last_posted_at is None or last_posted_at < occurrence
