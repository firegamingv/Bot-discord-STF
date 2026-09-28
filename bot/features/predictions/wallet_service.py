"""Portefeuille de points : capital de départ et bonus quotidien « paresseux ».

À chaque interaction « pronos », on appelle ``ensure_wallet`` :

1. première fois sur ce serveur → crédit du capital de départ (``starting_points``) ;
2. premier passage de la journée (jour local ``bot.config.timezone``) → crédit du bonus
   quotidien (``daily_points``), une seule fois par jour (vérifié en base, atomiquement).

La fonction renvoie un petit texte à afficher (« +100 🪙 bonus du jour ! ») ou ``None``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from bot.features.predictions.embeds import fmt_points
from bot.repositories.points import PointsRepository
from bot.repositories.users import UserRepository
from bot.services.periods import day_bounds

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


@dataclass(slots=True)
class WalletStatus:
    balance: int
    notice: str | None           # message de bienvenue / bonus à afficher, sinon None
    got_starting: bool = False
    got_daily: bool = False


async def ensure_wallet(bot: "STFBot", guild_id: int, user: discord.abc.User) -> WalletStatus:
    settings = await bot.settings.get(guild_id)
    points = PointsRepository(bot.db)
    await UserRepository(bot.db).ensure(user.id, getattr(user, "display_name", None))

    got_starting = await points.grant_starting(guild_id, user.id, settings.starting_points)
    start, end = day_bounds(bot.config.timezone)
    got_daily = settings.daily_points > 0 and await points.grant_daily(
        guild_id, user.id, settings.daily_points, day_start=start, day_end=end
    )

    parts: list[str] = []
    if got_starting:
        log.info("Portefeuille ouvert pour %s sur %s (+%d)", user.id, guild_id, settings.starting_points)
        parts.append(f"👋 Bienvenue dans les pronos ! **{fmt_points(settings.starting_points, signed=True)}** de capital de départ.")
    if got_daily:
        parts.append(f"☀️ **{fmt_points(settings.daily_points, signed=True)}** bonus du jour !")
    balance = await points.balance(guild_id, user.id)
    return WalletStatus(balance=balance, notice="\n".join(parts) or None,
                        got_starting=got_starting, got_daily=bool(got_daily))
