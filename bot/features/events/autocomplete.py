"""Autocomplétion des événements + résolution sûre d'un événement choisi.

Réutilisable par les autres domaines (ex. inhouse) :

```python
from bot.features.events.autocomplete import make_event_autocomplete, resolve_event

@app_commands.autocomplete(evenement=make_event_autocomplete(type="inhouse"))
async def ma_commande(interaction, evenement: int):
    event = await resolve_event(interaction, evenement, type="inhouse")
```
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from bot.core.errors import NotFoundError, UserFacingError
from bot.features.events.kinds import get_kind
from bot.repositories.events import Event, EventRepository

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

_WEEKDAYS_SHORT = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
_STATUS_SUFFIX = {"ongoing": " · 🟢 en cours", "finished": " · terminé", "cancelled": " · annulé"}

AutocompleteFn = Callable[[discord.Interaction, str], Awaitable[list[app_commands.Choice[int]]]]


def short_local_date(event: Event, tz: ZoneInfo) -> str:
    """Ex. « sam. 28/09 21:00 » dans le fuseau du bot."""
    local = event.starts_at.astimezone(tz)
    return f"{_WEEKDAYS_SHORT[local.weekday()]} {local:%d/%m %H:%M}"


def event_choice_label(event: Event, tz: ZoneInfo) -> str:
    """Libellé d'un choix d'autocomplétion : « #12 · 🎮 Titre · sam. 28/09 21:00 » (≤ 100 car.)."""
    emoji = get_kind(event.type).emoji
    suffix = f" · {short_local_date(event, tz)}{_STATUS_SUFFIX.get(event.status, '')}"
    prefix = f"#{event.id} · {emoji} "
    room = 100 - len(prefix) - len(suffix)
    title = event.title if len(event.title) <= room else event.title[: max(room - 1, 1)] + "…"
    return f"{prefix}{title}{suffix}"[:100]


def make_event_autocomplete(type: str | None = None, active_only: bool = True) -> AutocompleteFn:
    """Fabrique une fonction d'autocomplétion d'événements.

    - ``type`` : ne propose que ce type (``"inhouse"``…), ``None`` = tous ;
    - ``active_only`` : uniquement les événements programmés ou en cours.
    """

    async def autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        if interaction.guild_id is None:
            return []
        bot: STFBot = interaction.client  # type: ignore[assignment]
        try:
            events = await EventRepository(bot.db).search(
                interaction.guild_id, current.strip(), type=type, active_only=active_only, limit=25
            )
        except Exception:  # noqa: BLE001 - une autocomplétion ne doit jamais planter
            log.exception("Autocomplétion d'événement impossible")
            return []
        tz = bot.config.timezone
        return [app_commands.Choice(name=event_choice_label(e, tz), value=e.id) for e in events]

    return autocomplete


#: Autocomplétion par défaut : tous les types, événements actifs uniquement.
event_autocomplete: AutocompleteFn = make_event_autocomplete()
#: Variante incluant les événements terminés (utile pour consulter l'historique).
any_event_autocomplete: AutocompleteFn = make_event_autocomplete(active_only=False)


async def resolve_event(interaction: discord.Interaction, event_id: int, *, type: str | None = None) -> Event:
    """Récupère l'événement choisi dans ce serveur, ou lève une erreur claire."""
    bot: STFBot = interaction.client  # type: ignore[assignment]
    if interaction.guild_id is None:
        raise UserFacingError("Cette commande ne fonctionne que sur un serveur.")
    event = await EventRepository(bot.db).get_in_guild(interaction.guild_id, int(event_id))
    if event is None:
        raise NotFoundError(
            f"Je ne trouve pas l'événement **#{event_id}** sur ce serveur. "
            "Commence à taper son nom et choisis-le dans la liste proposée.",
            title="Événement introuvable",
        )
    if type is not None and event.type != type:
        kind = get_kind(type)
        raise NotFoundError(
            f"L'événement **{event.title}** (#{event.id}) n'est pas de type {kind.emoji} {kind.label}. "
            "Choisis un événement dans la liste proposée.",
            title="Mauvais type d'événement",
        )
    return event
