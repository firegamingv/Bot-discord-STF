"""Traitement centralisé des erreurs d'interaction (commandes, boutons, menus, modales)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import discord
from discord import app_commands

from bot.core.errors import UserFacingError
from bot.utils import embeds

log = logging.getLogger("bot.errors")


def _describe(interaction: discord.Interaction) -> str:
    cmd = interaction.command.qualified_name if interaction.command else None
    custom_id = (interaction.data or {}).get("custom_id") if interaction.data else None
    return (
        f"user={interaction.user} ({interaction.user.id}) guild={interaction.guild_id} "
        f"command={cmd} custom_id={custom_id}"
    )


async def report_error(interaction: discord.Interaction, error: BaseException) -> None:
    """Affiche un message clair à l'utilisateur et journalise ce qui doit l'être."""
    original = getattr(error, "original", error)

    if isinstance(original, UserFacingError):
        embed = embeds.error(original.message, title=original.title)
        log.debug("Erreur utilisateur (%s) : %s", _describe(interaction), original.message)
    elif isinstance(error, app_commands.CommandOnCooldown):
        embed = embeds.warning(
            f"Doucement ! Réessaie dans {error.retry_after:.0f} seconde(s)."
        )
    elif isinstance(error, app_commands.MissingPermissions):
        embed = embeds.error("Tu n'as pas les permissions nécessaires pour cette commande.")
    elif isinstance(error, app_commands.BotMissingPermissions):
        perms = ", ".join(error.missing_permissions)
        embed = embeds.error(f"Il me manque des permissions sur ce salon : `{perms}`.")
    elif isinstance(error, app_commands.NoPrivateMessage):
        embed = embeds.error("Cette commande ne fonctionne que sur un serveur.")
    elif isinstance(error, app_commands.CheckFailure):
        embed = embeds.error("Tu ne peux pas utiliser cette commande ici.")
    elif isinstance(original, discord.Forbidden):
        log.warning("Permission Discord manquante (%s) : %s", _describe(interaction), original)
        embed = embeds.error(
            "Je n'ai pas les permissions Discord nécessaires (envoyer des messages, "
            "intégrer des liens…). Vérifie mes rôles sur ce salon."
        )
    else:
        log.error("Erreur inattendue (%s)", _describe(interaction), exc_info=original)
        embed = embeds.error(
            "Oups, une erreur inattendue s'est produite. Elle a été enregistrée ; "
            "réessaie dans un instant ou préviens un organisateur."
        )

    try:
        await embeds.reply(interaction, embed, ephemeral=True)
    except discord.HTTPException:
        # Interaction expirée ou déjà répondue d'une façon incompatible : on journalise seulement.
        log.debug("Impossible de notifier l'utilisateur de l'erreur (%s)", _describe(interaction))


@asynccontextmanager
async def interaction_guard(interaction: discord.Interaction) -> AsyncIterator[None]:
    """À utiliser dans les callbacks de boutons/menus/modales :

    ``async with interaction_guard(interaction): ...``
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 - on veut tout rattraper ici
        await report_error(interaction, exc)


class BaseView(discord.ui.View):
    """Vue dont les erreurs sont affichées proprement à l'utilisateur."""

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        await report_error(interaction, error)


class BaseModal(discord.ui.Modal):
    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await report_error(interaction, error)
