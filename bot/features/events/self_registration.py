"""``/evenement rejoindre`` et ``/evenement quitter`` : alternatives aux boutons de l'annonce."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.features.events.autocomplete import event_autocomplete, event_choice_label
from bot.features.events.group import event_group
from bot.features.events.registration_service import join_event, leave_event
from bot.repositories.events import EventRepository
from bot.repositories.participants import ParticipantRepository

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


async def my_events_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """Seulement les événements actifs auxquels le membre est inscrit (ou en attente)."""
    if interaction.guild_id is None:
        return []
    bot: STFBot = interaction.client  # type: ignore[assignment]
    try:
        events = await EventRepository(bot.db).search(interaction.guild_id, current.strip(), limit=50)
        participants = ParticipantRepository(bot.db)
        choices: list[app_commands.Choice[int]] = []
        for event in events:
            p = await participants.get(event.id, interaction.user.id)
            if p is None:
                continue
            label = event_choice_label(event, bot.config.timezone)
            if p.on_waitlist:
                label = (label[:88] + " · ⏳ attente")[:100]
            choices.append(app_commands.Choice(name=label, value=event.id))
            if len(choices) >= 25:
                break
        return choices
    except Exception:  # noqa: BLE001
        log.exception("Autocomplétion « mes événements » impossible")
        return []


@event_group.command(name="rejoindre", description="T'inscrire à un événement")
@app_commands.describe(evenement="L'événement auquel t'inscrire (tape pour chercher)")
@app_commands.autocomplete(evenement=event_autocomplete)
async def join_command(interaction: discord.Interaction, evenement: int) -> None:
    await join_event(interaction.client, interaction, evenement)  # type: ignore[arg-type]


@event_group.command(name="quitter", description="Te désinscrire d'un événement")
@app_commands.describe(evenement="L'événement à quitter (seuls ceux où tu es inscrit·e sont proposés)")
@app_commands.autocomplete(evenement=my_events_autocomplete)
async def leave_command(interaction: discord.Interaction, evenement: int) -> None:
    await leave_event(interaction.client, interaction, evenement)  # type: ignore[arg-type]
