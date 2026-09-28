"""``/compte roles`` : choisir ses rôles préférés (ouvre le sélecteur)."""

from __future__ import annotations

import discord

from bot.features.account.group import account_group
from bot.features.account.role_picker import send_role_picker


@account_group.command(name="roles", description="Choisir tes rôles préférés (principal + secondaires)")
async def roles_command(interaction: discord.Interaction) -> None:
    await send_role_picker(interaction)
