"""``/pronos classement`` (gains nets, filtrable) et ``/pronos classement-general`` (solde)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.features.predictions.autocomplete import (
    competition_autocomplete,
    resolve_competition,
    tournament_autocomplete,
)
from bot.features.predictions.choices import BET_TYPE_CHOICES, PERIOD_CHOICES
from bot.features.predictions.group import pronos_group
from bot.features.predictions.leaderboard_service import render_balance_leaderboard, render_leaderboard
from bot.repositories.points import LeaderboardFilter
from bot.services.periods import PERIOD_ALL

if TYPE_CHECKING:
    from bot.core.bot import STFBot


async def _send(interaction: discord.Interaction, embed: discord.Embed, public: bool) -> None:
    if public:
        embed.set_footer(text=f"{embed.footer.text or ''} • demandé par {interaction.user.display_name}".strip(" •"))
    await interaction.response.send_message(
        embed=embed, ephemeral=not public, allowed_mentions=discord.AllowedMentions.none()
    )


@pronos_group.command(name="classement", description="🏆 Classement des pronostiqueurs (période, compétition, tournoi…)")
@app_commands.describe(
    periode="Période couverte (par défaut : depuis toujours)",
    competition="Limiter à une compétition",
    tournoi="Limiter à un tournoi / événement (ex. LEC Summer 2026)",
    type_pari="Limiter à un type de pari",
    public="Afficher le classement à tout le salon (par défaut : visible par toi seul)",
)
@app_commands.autocomplete(competition=competition_autocomplete, tournoi=tournament_autocomplete)
@app_commands.choices(periode=PERIOD_CHOICES, type_pari=BET_TYPE_CHOICES)
async def leaderboard(
    interaction: discord.Interaction,
    periode: app_commands.Choice[str] | None = None,
    competition: str | None = None,
    tournoi: str | None = None,
    type_pari: app_commands.Choice[str] | None = None,
    public: bool = False,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild_id: int = interaction.guild_id  # type: ignore[assignment]
    comp = await resolve_competition(bot, guild_id, competition)
    flt = LeaderboardFilter(
        period=periode.value if periode else PERIOD_ALL,
        competition_id=comp.id if comp else None,
        tournament_name=tournoi.strip() if tournoi and tournoi.strip() else None,
        bet_type=type_pari.value if type_pari else None,
    )
    embed = await render_leaderboard(bot, guild_id, flt, me_id=None if public else interaction.user.id)
    await _send(interaction, embed, public)


@pronos_group.command(name="classement-general", description="💰 Classement général : les plus gros soldes du serveur")
@app_commands.describe(public="Afficher le classement à tout le salon (par défaut : visible par toi seul)")
async def balance_leaderboard(interaction: discord.Interaction, public: bool = False) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    embed = await render_balance_leaderboard(
        bot, interaction.guild_id, me_id=None if public else interaction.user.id  # type: ignore[arg-type]
    )
    await _send(interaction, embed, public)
