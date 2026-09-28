"""``/evenement inscriptions`` : ouvrir ou fermer les inscriptions d'un événement.

``set_registration(bot, event, open)`` est réutilisable par les autres types (ex. inhouse).
"""

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
from bot.repositories.events import Event, EventRepository
from bot.utils import embeds

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


async def set_registration(bot: "STFBot", event: Event, open: bool) -> Event:
    """Ouvre/ferme les inscriptions et met à jour l'annonce. Renvoie l'événement à jour."""
    updated = await EventRepository(bot.db).update(event.id, registration_open=open)
    assert updated is not None
    log.info("Inscriptions %s pour l'événement %s", "ouvertes" if open else "fermées", event.id)
    await refresh_event_message(bot, event.id)
    return updated


@event_group.command(name="inscriptions", description="Ouvrir ou fermer les inscriptions d'un événement")
@app_commands.describe(evenement="L'événement (tape pour chercher)", etat="Ouvrir ou fermer les inscriptions")
@app_commands.choices(
    etat=[
        app_commands.Choice(name="🔓 Ouvrir", value="ouvrir"),
        app_commands.Choice(name="🔒 Fermer", value="fermer"),
    ]
)
@app_commands.autocomplete(evenement=event_autocomplete)
@organizer_only()
async def toggle_registration(
    interaction: discord.Interaction, evenement: int, etat: app_commands.Choice[str]
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event = await resolve_event(interaction, evenement)
    open_ = etat.value == "ouvrir"
    if not event.is_active:
        raise UserFacingError(f"**{event.title}** est terminé : impossible de changer les inscriptions.")
    if open_ and event.has_started:
        raise UserFacingError(
            f"**{event.title}** a déjà commencé : les inscriptions ne peuvent pas être rouvertes. "
            "Si l'événement est reporté, change d'abord sa date avec `/evenement modifier`."
        )
    if event.registration_open == open_:
        state = "déjà ouvertes 🔓" if open_ else "déjà fermées 🔒"
        await embeds.reply(interaction, embeds.info(f"Les inscriptions à **{event.title}** sont {state}."))
        return

    await set_registration(bot, event, open_)
    if open_:
        text = f"Inscriptions **ouvertes** 🔓 pour **{event.title}** : le bouton « Rejoindre » est actif."
    else:
        text = (
            f"Inscriptions **fermées** 🔒 pour **{event.title}**. Les inscrits gardent leur place "
            "et peuvent toujours se désinscrire."
        )
    await embeds.reply(interaction, embeds.success(text), ephemeral=True)
