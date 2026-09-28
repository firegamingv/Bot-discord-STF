"""Lecture des délais de rappel saisis par un humain (sans dépendance à Discord).

Exemples acceptés : ``"1j, 1h, 15min"``, ``"2 jours; 3 heures"``, ``"1h30 / 10m"``,
``"45"`` (minutes par défaut), ``"aucun"`` (désactive les rappels).
"""

from __future__ import annotations

import re

MIN_MINUTES = 1
MAX_MINUTES = 7 * 24 * 60  # 7 jours
MAX_REMINDERS = 5

FORMAT_HELP = (
    "Exemples : `1j, 1h, 15min` • `2h30, 30min` • `aucun` pour désactiver. "
    "Unités : `j` (jours), `h` (heures), `min` (minutes)."
)

_UNITS: dict[str, int] = {
    "j": 1440, "jr": 1440, "jrs": 1440, "jour": 1440, "jours": 1440, "d": 1440, "day": 1440, "days": 1440,
    "h": 60, "hr": 60, "hrs": 60, "heure": 60, "heures": 60, "hour": 60, "hours": 60,
    "m": 1, "mn": 1, "min": 1, "mins": 1, "minute": 1, "minutes": 1,
}
_PART_RE = re.compile(r"(\d+)\s*([a-zé]*)")
_GLUED_RE = re.compile(r"^(?:\d+[a-zé]+)+\d*$")
_DISABLE_WORDS = {"aucun", "aucune", "rien", "non", "off", "0", "desactiver", "désactiver", "none"}


class ReminderParseError(ValueError):
    """Saisie illisible ou hors limites ; le message est destiné à l'utilisateur."""


def parse_duration(token: str) -> int:
    """Convertit un délai (``"1j"``, ``"1h30"``, ``"15 min"``, ``"45"``) en minutes."""
    raw = token.strip().lower()
    compact = raw.replace(" ", "")
    if not compact:
        raise ReminderParseError(f"Délai vide. {FORMAT_HELP}")

    total = 0
    pos = 0
    previous_unit: int | None = None
    for match in _PART_RE.finditer(compact):
        if match.start() != pos:
            break
        value, unit = int(match.group(1)), match.group(2)
        if unit:
            if unit not in _UNITS:
                raise ReminderParseError(f"Unité inconnue « {unit} » dans `{token.strip()}`. {FORMAT_HELP}")
            factor = _UNITS[unit]
        elif previous_unit == 1440:
            factor = 60  # "1j12" => 1 jour 12 heures
        elif previous_unit == 60:
            factor = 1  # "1h30" => 1 heure 30 minutes
        elif previous_unit is None:
            factor = 1  # nombre seul => minutes
        else:
            raise ReminderParseError(f"Délai illisible : `{token.strip()}`. {FORMAT_HELP}")
        total += value * factor
        previous_unit = factor
        pos = match.end()
    if pos != len(compact) or pos == 0:
        raise ReminderParseError(f"Délai illisible : `{token.strip()}`. {FORMAT_HELP}")
    return total


def parse_reminder_offsets(text: str) -> list[int]:
    """Renvoie la liste des délais en minutes, triée du plus long au plus court, sans doublon.

    Liste vide si l'utilisateur a demandé à désactiver les rappels (``"aucun"``).
    """
    cleaned = (text or "").strip().lower()
    if cleaned in _DISABLE_WORDS:
        return []
    tokens = [t for t in re.split(r"[,;/|]|\s+et\s+", cleaned) if t.strip()]
    if len(tokens) == 1:
        # "1j 1h 15min" (sans virgule) = 3 rappels, mais "2 jours 3 heures" / "1h 30" = 1 seul délai.
        words = tokens[0].split()
        if len(words) > 1 and all(_GLUED_RE.match(w) for w in words):
            tokens = words
    if not tokens:
        raise ReminderParseError(f"Aucun délai trouvé. {FORMAT_HELP}")

    offsets: set[int] = set()
    for token in tokens:
        minutes = parse_duration(token)
        if minutes < MIN_MINUTES:
            raise ReminderParseError(f"`{token.strip()}` est trop court : minimum 1 minute.")
        if minutes > MAX_MINUTES:
            raise ReminderParseError(f"`{token.strip()}` est trop long : maximum 7 jours avant l'événement.")
        offsets.add(minutes)
    if len(offsets) > MAX_REMINDERS:
        raise ReminderParseError(
            f"Tu as indiqué {len(offsets)} rappels : {MAX_REMINDERS} maximum pour ne pas spammer les membres."
        )
    return sorted(offsets, reverse=True)
