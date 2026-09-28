"""``/evenement terminer`` : clôturer un événement à la main (boutons désactivés)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.checks import organizer_only
from bot.core.errors import UserFacingError
from bot.features.events.announcement import refresh_event_message
from bot.features.events.autocomplete import event_autocomplete, resolve_event
from bot.features.events.group import event_group
from bot.repositories.events import STATUS_FINISHED, Event, EventRepository
from bot.repositories.participants import ParticipantRepository
from bot.utils import embeds

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


async def finish_event(bot: "STFBot", event: Event) -> Event:
    """Passe l'événement en « terminé », ferme les inscriptions et met à jour l'annonce."""
    updated = await EventRepository(bot.db).update(event.id, status=STATUS_FINISHED, registration_open=False)
    assert updated is not None
    log.info("Événement %s terminé", event.id)
    await refresh_event_message(bot, event.id)
    return updated


@event_group.command(name="terminer", description="Marquer un événement comme terminé")
@app_commands.describe(evenement="L'événement à clôturer (tape pour chercher)")
@app_commands.autocomplete(evenement=event_autocomplete)
@organizer_only()
async def finish_command(interaction: discord.Interaction, evenement: int) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event = await resolve_event(interaction, evenement)
    if not event.is_active:
        raise UserFacingError(f"**{event.title}** est déjà terminé.")
    # La mise à jour de l'annonce (API Discord) peut dépasser les 3 secondes.
    await interaction.response.defer(ephemeral=True, thinking=True)
    await finish_event(bot, event)
    count = await ParticipantRepository(bot.db).count(event.id)
    log.info("Clôture manuelle de l'événement %s par %s (%s)", event.id, interaction.user, interaction.user.id)
    await embeds.reply(
        interaction,
        embeds.success(
            f"**{event.title}** est terminé 🏁 ({count} participant(s)). "
            "L'annonce a été mise à jour et les boutons désactivés. Merci pour l'organisation !"
        ),
        ephemeral=True,
    )
