"""Construction des classements (utilisée par la commande manuelle et la publication automatique)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.features.predictions.embeds import build_balance_leaderboard_embed, build_leaderboard_embed
from bot.repositories.competitions import CompetitionRepository
from bot.repositories.points import LeaderboardFilter, PointsRepository

if TYPE_CHECKING:
    from bot.core.bot import STFBot

LEADERBOARD_SIZE = 10


async def render_leaderboard(
    bot: "STFBot", guild_id: int, flt: LeaderboardFilter, *, me_id: int | None = None,
    limit: int = LEADERBOARD_SIZE,
) -> discord.Embed:
    """Classement par gains nets selon le filtre (période, compétition, tournoi, type de pari)."""
    points = PointsRepository(bot.db)
    tz = bot.config.timezone
    entries = await points.leaderboard(guild_id, flt, tz, limit=limit)
    me = await points.leaderboard_rank(guild_id, me_id, flt, tz) if me_id is not None else None
    competition = (
        await CompetitionRepository(bot.db).get_in_guild(guild_id, flt.competition_id)
        if flt.competition_id is not None else None
    )
    return build_leaderboard_embed(entries, flt, competition, me=me, me_id=me_id)


async def render_balance_leaderboard(
    bot: "STFBot", guild_id: int, *, me_id: int | None = None, limit: int = LEADERBOARD_SIZE
) -> discord.Embed:
    """Classement général : solde actuel des membres."""
    points = PointsRepository(bot.db)
    entries = await points.balance_leaderboard(guild_id, limit=limit)
    me = await points.balance_rank(guild_id, me_id) if me_id is not None else None
    my_balance = await points.balance(guild_id, me_id) if me_id is not None else None
    return build_balance_leaderboard_embed(entries, me=me, me_id=me_id, my_balance=my_balance)
