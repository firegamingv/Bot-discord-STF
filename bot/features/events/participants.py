"""``/evenement participants`` (tout le monde) et ``/evenement retirer`` (organisateurs)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands

from bot.core.checks import organizer_only
from bot.core.errors import UserFacingError
from bot.features.events.announcement import build_participants_embed
from bot.features.events.autocomplete import any_event_autocomplete, event_autocomplete, resolve_event
from bot.features.events.group import event_group
from bot.features.events.notifications import notify_members
from bot.features.events.registration_service import remove_participant
from bot.utils import embeds
from bot.utils.embeds import Colors

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


@event_group.command(name="participants", description="Voir la liste des inscrits et de la liste d'attente")
@app_commands.describe(evenement="L'événement (tape pour chercher)")
@app_commands.autocomplete(evenement=any_event_autocomplete)
async def list_participants(interaction: discord.Interaction, evenement: int) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event = await resolve_event(interaction, evenement)
    embed = await build_participants_embed(bot, event)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@event_group.command(name="retirer", description="Retirer un membre d'un événement (organisateurs)")
@app_commands.describe(
    evenement="L'événement (tape pour chercher)",
    membre="Le membre à retirer des inscrits ou de la liste d'attente",
    raison="Raison transmise au membre en MP (facultatif)",
    prevenir="Prévenir le membre en MP (oui par défaut)",
)
@app_commands.autocomplete(evenement=event_autocomplete)
@organizer_only()
async def remove_member(
    interaction: discord.Interaction,
    evenement: int,
    membre: discord.User,
    raison: Optional[app_commands.Range[str, 1, 300]] = None,
    prevenir: bool = True,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event = await resolve_event(interaction, evenement)
    if not event.is_active:
        raise UserFacingError(f"**{event.title}** est terminé : la liste des participants est figée.")

    await interaction.response.defer(ephemeral=True, thinking=True)
    result = await remove_participant(bot, event, membre.id)
    if result.removed is None:
        raise UserFacingError(f"{membre.mention} n'est pas inscrit·e à **{event.title}** : rien à retirer.")
    log.info("%s retiré de l'événement %s par %s (%s)", membre.id, event.id, interaction.user, interaction.user.id)

    where = "de la liste d'attente" if result.removed.on_waitlist else "des inscrits"
    lines = [f"{membre.mention} a été retiré·e {where} de **{event.title}**."]
    if result.promoted:
        promoted = ", ".join(f"<@{uid}>" for uid in result.promoted)
        lines.append(f"🎉 Place attribuée à {promoted} (prévenu·e·s).")
    if prevenir and not membre.bot:
        text = f"Un organisateur t'a retiré·e de **{event.title}**."
        if raison:
            text += f"\n> {raison}"
        await notify_members(
            bot, result.event, [membre.id],
            title="👋 Tu as été retiré·e d'un événement", text=text, color=Colors.WARNING,
        )
        lines.append("📨 Le membre a été prévenu.")
    await embeds.reply(interaction, embeds.success("\n".join(lines), title="Participant retiré"), ephemeral=True)
