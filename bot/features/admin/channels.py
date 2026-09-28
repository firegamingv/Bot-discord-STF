"""``/config salon-annonces`` et ``/config salon-pronos`` : salons où le bot publie."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.checks import guild_only_check, organizer_only
from bot.core.errors import UserFacingError
from bot.features.admin.channel_permissions import missing_permissions_hint, missing_post_permissions
from bot.features.admin.group import config_group
from bot.utils import embeds

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


def _resolve(interaction: discord.Interaction, salon: discord.abc.GuildChannel) -> discord.abc.GuildChannel:
    guild = guild_only_check(interaction)
    channel = guild.get_channel(salon.id)
    if channel is None:
        raise UserFacingError(
            "Je ne vois pas ce salon. Vérifie que j'ai la permission **Voir le salon**, puis réessaie."
        )
    return channel


async def _set_channel(
    interaction: discord.Interaction, salon: discord.TextChannel, *, setting: str, purpose: str, next_tip: str
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    channel = _resolve(interaction, salon)
    missing = missing_post_permissions(channel)
    if missing:
        raise UserFacingError(missing_permissions_hint(channel, missing), title="Permissions manquantes")
    await bot.settings.update(channel.guild.id, **{setting: channel.id})
    log.info("Salon %s configuré : #%s (%s) sur %s par %s", setting, channel.name, channel.id,
             channel.guild.id, interaction.user)
    await embeds.reply(
        interaction,
        embeds.success(f"{purpose} seront publiés dans {channel.mention}.\n{next_tip}", title="Salon enregistré"),
    )


@config_group.command(name="salon-annonces", description="Choisir le salon des annonces d'événements et d'inhouses")
@app_commands.describe(salon="Salon où seront publiées les annonces, rappels et équipes")
@organizer_only()
async def set_announce_channel(interaction: discord.Interaction, salon: discord.TextChannel) -> None:
    await _set_channel(
        interaction, salon,
        setting="announce_channel_id",
        purpose="📣 Les annonces d'événements, rappels et équipes d'inhouse",
        next_tip="Tu peux maintenant créer un événement avec `/evenement creer` ou `/inhouse creer`.",
    )


@config_group.command(name="salon-pronos", description="Choisir le salon des pronostics esport")
@app_commands.describe(salon="Salon où seront publiés les matchs du jour, résultats et classements")
@organizer_only()
async def set_predictions_channel(interaction: discord.Interaction, salon: discord.TextChannel) -> None:
    await _set_channel(
        interaction, salon,
        setting="predictions_channel_id",
        purpose="🎯 Les matchs à parier, résultats et classements des pronostics",
        next_tip="Choisis ensuite les compétitions suivies avec `/pronos-admin competitions`.",
    )
