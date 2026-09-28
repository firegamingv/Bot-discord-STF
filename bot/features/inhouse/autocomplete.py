"""Autocomplétion des sessions d'inhouse et des équipes + résolution sûre d'une session."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.features.events.autocomplete import make_event_autocomplete, resolve_event
from bot.features.inhouse.constants import EVENT_TYPE, SUBSTITUTES_EMOJI, get_mode, team_title
from bot.repositories.events import Event
from bot.repositories.inhouse import InhouseRepository, InhouseSession
from bot.repositories.inhouse_teams import InhouseTeamRepository

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

#: Sessions d'inhouse actives (programmées ou en cours).
inhouse_autocomplete = make_event_autocomplete(type=EVENT_TYPE)


async def resolve_inhouse(interaction: discord.Interaction, event_id: int) -> tuple[Event, InhouseSession]:
    """Événement + session d'inhouse choisis, ou erreur claire (``NotFoundError``)."""
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event = await resolve_event(interaction, event_id, type=EVENT_TYPE)
    repo = InhouseRepository(bot.db)
    session = await repo.get(event.id)
    if session is None:
        # Ne devrait pas arriver (création atomique côté /inhouse creer) : on répare en Faille.
        log.warning("Session d'inhouse manquante pour l'événement %s : recréée en mode sr", event.id)
        session = await repo.create(event.id, "sr")
    return event, session


async def team_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """Équipes de la session choisie dans l'option ``session`` (0 = remplaçants)."""
    try:
        bot: STFBot = interaction.client  # type: ignore[assignment]
        event_id = getattr(interaction.namespace, "session", None)
        if not event_id:
            return []
        session = await InhouseRepository(bot.db).get(int(event_id))
        if session is None:
            return []
        mode = get_mode(session.game_mode)
        stored = await InhouseTeamRepository(bot.db).get_teams(int(event_id))
        choices: list[app_commands.Choice[int]] = []
        for index in range(stored.team_count):
            members = stored.team(index)
            names = []
            for m in members[:5]:
                user = bot.get_user(m.discord_id)
                names.append(user.display_name if user else str(m.discord_id))
            label = f"{team_title(index, mode.key)} ({len(members)}/{mode.team_size}) — {', '.join(names) or 'vide'}"
            choices.append(app_commands.Choice(name=label[:100], value=index + 1))
        choices.append(
            app_commands.Choice(name=f"{SUBSTITUTES_EMOJI} Remplaçants ({len(stored.substitutes)})", value=0)
        )
        text = current.strip().lower()
        if text:
            choices = [c for c in choices if text in c.name.lower()] or choices
        return choices[:25]
    except Exception:  # noqa: BLE001 - une autocomplétion ne doit jamais planter
        log.exception("Autocomplétion d'équipe impossible")
        return []
