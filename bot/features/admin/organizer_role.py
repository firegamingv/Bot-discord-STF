"""``/config role-organisateur [role]`` : rôle autorisé à gérer événements, inhouses et pronos."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.checks import guild_only_check, organizer_only
from bot.core.errors import UserFacingError
from bot.features.admin.group import config_group
from bot.utils import embeds

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


@config_group.command(
    name="role-organisateur",
    description="Définir le rôle organisateur (laisser vide pour le retirer)",
)
@app_commands.describe(role="Rôle qui pourra gérer événements, inhouses et pronostics (vide = retirer)")
@organizer_only()
async def set_organizer_role(interaction: discord.Interaction, role: discord.Role | None = None) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild = guild_only_check(interaction)

    if role is None:
        await bot.settings.update(guild.id, organizer_role_id=None)
        log.info("Rôle organisateur retiré sur %s par %s", guild.id, interaction.user)
        await embeds.reply(
            interaction,
            embeds.success(
                "Rôle organisateur retiré : seuls les membres avec la permission *Gérer le serveur* "
                "peuvent désormais organiser.",
                title="Rôle organisateur",
            ),
        )
        return

    if role.is_default():
        raise UserFacingError(
            "Impossible d'utiliser @everyone comme rôle organisateur : tout le monde pourrait "
            "supprimer les événements ! Choisis un rôle dédié (ex. « Staff »)."
        )
    if role.managed:
        raise UserFacingError(
            f"{role.mention} est géré par une intégration (bot, boost…) et ne peut pas être attribué "
            "manuellement. Choisis un rôle classique."
        )

    await bot.settings.update(guild.id, organizer_role_id=role.id)
    log.info("Rôle organisateur = %s (%s) sur %s par %s", role.name, role.id, guild.id, interaction.user)
    embed = embeds.success(
        f"Les membres ayant le rôle {role.mention} peuvent maintenant créer et gérer les événements, "
        "inhouses et pronostics.",
        title="Rôle organisateur",
    )
    embed.add_field(
        name="💡 Bon à savoir",
        value=(
            "Les commandes `/config` restent masquées aux membres sans *Gérer le serveur*. Pour les "
            "rendre visibles à ce rôle : *Paramètres du serveur > Intégrations >* mon nom *> /config*."
        ),
        inline=False,
    )
    await embeds.reply(interaction, embed)
