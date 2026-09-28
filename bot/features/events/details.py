"""``/evenement voir`` : toutes les infos d'un événement, en privé, avec les boutons d'inscription."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.features.events.announcement import build_event_embed, build_event_view, event_message_url, link_button
from bot.features.events.autocomplete import any_event_autocomplete, resolve_event
from bot.features.events.group import event_group

if TYPE_CHECKING:
    from bot.core.bot import STFBot


@event_group.command(name="voir", description="Afficher les détails d'un événement")
@app_commands.describe(evenement="L'événement à afficher (tape pour chercher)")
@app_commands.autocomplete(evenement=any_event_autocomplete)
async def show_event(interaction: discord.Interaction, evenement: int) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event = await resolve_event(interaction, evenement)
    embed = await build_event_embed(bot, event)
    # Les boutons persistants fonctionnent aussi ici : on peut s'inscrire sans chercher l'annonce.
    view = await build_event_view(bot, event)
    if url := event_message_url(event):
        view.add_item(link_button(url))
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
