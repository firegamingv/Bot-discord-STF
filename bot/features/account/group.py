"""Groupe de commandes slash ``/compte`` (liaison du compte League of Legends)."""

from __future__ import annotations

from discord import app_commands

account_group = app_commands.Group(
    name="compte",
    description="Ton compte League of Legends : liaison Riot ID, rôles préférés, profil",
    guild_only=True,
)
