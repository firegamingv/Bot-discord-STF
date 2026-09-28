"""Vérifications de permissions réutilisables."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.errors import PermissionDeniedError

if TYPE_CHECKING:
    from bot.core.bot import STFBot


async def is_organizer(interaction: discord.Interaction) -> bool:
    """Organisateur = permission « Gérer le serveur » OU rôle organisateur configuré."""
    member = interaction.user
    if not isinstance(member, discord.Member) or interaction.guild is None:
        return False
    if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
        return True
    bot: STFBot = interaction.client  # type: ignore[assignment]
    settings = await bot.settings.get(interaction.guild.id)
    role_id = settings.organizer_role_id
    return role_id is not None and any(r.id == role_id for r in member.roles)


async def ensure_organizer(interaction: discord.Interaction) -> None:
    """Version impérative (utile dans les boutons)."""
    if not await is_organizer(interaction):
        raise PermissionDeniedError(
            "Cette action est réservée aux organisateurs "
            "(permission *Gérer le serveur* ou rôle organisateur)."
        )


def organizer_only():
    """Décorateur pour les commandes slash réservées aux organisateurs."""

    async def predicate(interaction: discord.Interaction) -> bool:
        await ensure_organizer(interaction)
        return True

    return app_commands.check(predicate)


def guild_only_check(interaction: discord.Interaction) -> discord.Guild:
    if interaction.guild is None:
        raise PermissionDeniedError("Cette commande ne fonctionne que sur un serveur.")
    return interaction.guild
