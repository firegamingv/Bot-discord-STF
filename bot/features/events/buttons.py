"""Boutons persistants des annonces d'événements (survivent aux redémarrages du bot).

Identifiants : ``evt:join:<id>``, ``evt:leave:<id>``, ``evt:list:<id>``.
Enregistrés dans ``__init__.setup`` via ``bot.add_dynamic_items``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import discord

from bot.core.error_reporting import interaction_guard
from bot.core.errors import NotFoundError
from bot.features.events.announcement import build_participants_embed
from bot.features.events.registration_service import join_event, leave_event
from bot.repositories.events import EventRepository

if TYPE_CHECKING:
    from bot.core.bot import STFBot


class JoinButton(discord.ui.DynamicItem[discord.ui.Button], template=r"evt:join:(?P<id>[0-9]+)"):
    """« Rejoindre » : inscrit le membre (ou le met en liste d'attente)."""

    def __init__(self, event_id: int, *, disabled: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label="Rejoindre",
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=f"evt:join:{event_id}",
                disabled=disabled,
            )
        )
        self.event_id = event_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ) -> "JoinButton":
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        async with interaction_guard(interaction):
            await join_event(interaction.client, interaction, self.event_id)  # type: ignore[arg-type]


class LeaveButton(discord.ui.DynamicItem[discord.ui.Button], template=r"evt:leave:(?P<id>[0-9]+)"):
    """« Quitter » : désinscrit le membre et fait monter la liste d'attente."""

    def __init__(self, event_id: int, *, disabled: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label="Quitter",
                emoji="🚪",
                style=discord.ButtonStyle.secondary,
                custom_id=f"evt:leave:{event_id}",
                disabled=disabled,
            )
        )
        self.event_id = event_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ) -> "LeaveButton":
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        async with interaction_guard(interaction):
            await leave_event(interaction.client, interaction, self.event_id)  # type: ignore[arg-type]


class ParticipantsButton(discord.ui.DynamicItem[discord.ui.Button], template=r"evt:list:(?P<id>[0-9]+)"):
    """« Voir les inscrits » : liste détaillée, visible uniquement par celui qui clique."""

    def __init__(self, event_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Voir les inscrits",
                emoji="👥",
                style=discord.ButtonStyle.primary,
                custom_id=f"evt:list:{event_id}",
            )
        )
        self.event_id = event_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ) -> "ParticipantsButton":
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        async with interaction_guard(interaction):
            bot: STFBot = interaction.client  # type: ignore[assignment]
            event = await EventRepository(bot.db).get(self.event_id)
            if event is None or event.guild_id != interaction.guild_id:
                raise NotFoundError("Cet événement n'existe plus (il a peut-être été supprimé).")
            embed = await build_participants_embed(bot, event)
            await interaction.response.send_message(embed=embed, ephemeral=True)
