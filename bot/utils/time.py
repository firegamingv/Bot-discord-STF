"""Gestion des dates : parsing en français, conversion UTC <-> base, timestamps Discord."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc

_WEEKDAYS = {
    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
    "vendredi": 4, "samedi": 5, "dimanche": 6,
}

_TIME_RE = re.compile(r"^(?P<h>\d{1,2})\s*(?:[h:]\s*(?P<m>\d{2})?)?$")
_DATE_RE = re.compile(r"^(?P<d>\d{1,2})[/.-](?P<mo>\d{1,2})(?:[/.-](?P<y>\d{2,4}))?$")
_ISO_RE = re.compile(r"^(?P<y>\d{4})-(?P<mo>\d{1,2})-(?P<d>\d{1,2})$")


class DateParseError(ValueError):
    """Date saisie par l'utilisateur illisible."""


DATE_HELP = (
    "Formats acceptés : `28/09/2026 21:00`, `28/09 21h`, `2026-09-28 21:00`, "
    "`demain 21h`, `aujourd'hui 20h30`, `samedi 18h`."
)


def now_utc() -> datetime:
    return datetime.now(UTC)


def _parse_time(raw: str) -> time:
    m = _TIME_RE.match(raw.strip().lower())
    if not m:
        raise DateParseError(f"Heure illisible : `{raw}`. {DATE_HELP}")
    h, mi = int(m["h"]), int(m["m"] or 0)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise DateParseError(f"Heure invalide : `{raw}`.")
    return time(h, mi)


def _parse_day(raw: str, today: date) -> date:
    word = raw.strip().lower().replace("’", "'")
    if word in ("aujourd'hui", "aujourdhui", "ajd", "auj"):
        return today
    if word == "demain":
        return today + timedelta(days=1)
    if word in ("après-demain", "apres-demain", "apres demain", "après demain"):
        return today + timedelta(days=2)
    if word in _WEEKDAYS:
        delta = (_WEEKDAYS[word] - today.weekday()) % 7
        return today + timedelta(days=delta or 7)
    if m := _ISO_RE.match(word):
        return _safe_date(int(m["y"]), int(m["mo"]), int(m["d"]), raw)
    if m := _DATE_RE.match(word):
        year = today.year
        if m["y"]:
            year = int(m["y"])
            if year < 100:
                year += 2000
        d = _safe_date(year, int(m["mo"]), int(m["d"]), raw)
        if not m["y"] and d < today:  # "05/01" en décembre => année suivante
            d = _safe_date(year + 1, d.month, d.day, raw)
        return d
    raise DateParseError(f"Jour illisible : `{raw}`. {DATE_HELP}")


def _safe_date(y: int, mo: int, d: int, raw: str) -> date:
    try:
        return date(y, mo, d)
    except ValueError as exc:
        raise DateParseError(f"Date invalide : `{raw}`.") from exc


def parse_user_datetime(raw: str, tz: ZoneInfo, *, now: datetime | None = None) -> datetime:
    """Parse une date saisie par un humain (heure locale ``tz``) et renvoie un datetime UTC."""
    text = " ".join(raw.strip().replace(" à ", " ").replace(" a ", " ").split())
    if not text:
        raise DateParseError(f"Date vide. {DATE_HELP}")
    local_now = (now or now_utc()).astimezone(tz)

    # On sépare le dernier bloc (l'heure) du reste (le jour). "apres demain 21h" => 2 mots de jour.
    parts = text.rsplit(" ", 1)
    if len(parts) == 1:
        # Uniquement une heure => aujourd'hui (ou demain si déjà passée)
        t = _parse_time(parts[0])
        d = local_now.date()
        candidate = datetime.combine(d, t, tzinfo=tz)
        if candidate <= local_now:
            candidate = datetime.combine(d + timedelta(days=1), t, tzinfo=tz)
        return candidate.astimezone(UTC)

    day_part, time_part = parts
    d = _parse_day(day_part, local_now.date())
    t = _parse_time(time_part)
    return datetime.combine(d, t, tzinfo=tz).astimezone(UTC)


# ---------------------------------------------------------------------- base de données
def to_db(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def from_db(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


# ---------------------------------------------------------------------- affichage Discord
def discord_ts(dt: datetime, style: str = "F") -> str:
    """Timestamp Discord affiché dans le fuseau de chaque membre.

    Styles : t (21:00), T, d (28/09/2026), D, f, F (samedi 28 septembre 2026 21:00), R (dans 2 heures).
    """
    return f"<t:{int(dt.timestamp())}:{style}>"


def discord_full(dt: datetime) -> str:
    """Date complète + temps restant, ex. « samedi 28 septembre 21:00 (dans 2 heures) »."""
    return f"{discord_ts(dt, 'F')} ({discord_ts(dt, 'R')})"


def format_local(dt: datetime, tz: ZoneInfo, fmt: str = "%d/%m/%Y %H:%M") -> str:
    return dt.astimezone(tz).strftime(fmt)


def humanize_minutes(minutes: int) -> str:
    if minutes % 1440 == 0:
        n = minutes // 1440
        return f"{n} jour{'s' if n > 1 else ''}"
    if minutes % 60 == 0:
        n = minutes // 60
        return f"{n} heure{'s' if n > 1 else ''}"
    return f"{minutes} minute{'s' if minutes > 1 else ''}"
