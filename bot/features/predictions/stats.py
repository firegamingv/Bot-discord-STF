"""``/pronos stats [membre] [competition] [periode]`` : statistiques de pronostics."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.features.predictions.autocomplete import competition_autocomplete, resolve_competition
from bot.features.predictions.choices import PERIOD_CHOICES
from bot.features.predictions.embeds import build_stats_embed
from bot.features.predictions.group import pronos_group
from bot.features.predictions.wallet_service import ensure_wallet
from bot.repositories.points import LeaderboardFilter, PointsRepository
from bot.services.periods import PERIOD_ALL

if TYPE_CHECKING:
    from bot.core.bot import STFBot


@pronos_group.command(name="stats", description="📊 Statistiques de pronostics (les tiennes ou celles d'un membre)")
@app_commands.describe(
    membre="Le membre à analyser (toi par défaut)",
    competition="Limiter à une compétition",
    periode="Limiter à une période (par défaut : depuis toujours)",
)
@app_commands.autocomplete(competition=competition_autocomplete)
@app_commands.choices(periode=PERIOD_CHOICES)
async def stats(
    interaction: discord.Interaction,
    membre: discord.Member | None = None,
    competition: str | None = None,
    periode: app_commands.Choice[str] | None = None,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild_id: int = interaction.guild_id  # type: ignore[assignment]
    target = membre or interaction.user
    if target.bot:
        await interaction.response.send_message("🤖 Les bots ne parient pas (enfin… pas encore).", ephemeral=True)
        return
    if target.id == interaction.user.id:
        await ensure_wallet(bot, guild_id, interaction.user)
    comp = await resolve_competition(bot, guild_id, competition)
    flt = LeaderboardFilter(period=periode.value if periode else PERIOD_ALL,
                            competition_id=comp.id if comp else None)
    points = PointsRepository(bot.db)
    user_stats = await points.user_stats(guild_id, target.id, flt, bot.config.timezone)
    balance = await points.balance(guild_id, target.id)
    embed = build_stats_embed(target, user_stats, flt, comp, balance=balance)
    rank = await points.leaderboard_rank(guild_id, target.id, flt, bot.config.timezone)
    if rank:
        embed.add_field(name="🏅 Classement (même filtre)", value=f"**{rank[0]}ᵉ** sur {rank[1]}", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)
