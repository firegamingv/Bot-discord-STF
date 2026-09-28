"""``/pronos solde`` : solde, bonus du jour, rang au classement général, derniers mouvements."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.features.predictions.embeds import fmt_points
from bot.features.predictions.group import pronos_group
from bot.features.predictions.wallet_service import ensure_wallet
from bot.repositories.points import KIND_LABELS, PointsRepository
from bot.repositories.predictions import STATUS_PENDING, PredictionRepository
from bot.services.periods import day_bounds
from bot.utils.embeds import Colors, Emojis
from bot.utils.time import discord_ts

if TYPE_CHECKING:
    from bot.core.bot import STFBot


@pronos_group.command(name="solde", description="🪙 Voir ton solde de points et ton rang")
async def balance(interaction: discord.Interaction) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild_id: int = interaction.guild_id  # type: ignore[assignment]
    wallet = await ensure_wallet(bot, guild_id, interaction.user)
    points = PointsRepository(bot.db)
    settings = await bot.settings.get(guild_id)

    pending = await PredictionRepository(bot.db).list_for_user(
        guild_id, interaction.user.id, statuses=(STATUS_PENDING,), limit=100
    )
    engaged = sum(p.stake for p in pending)
    rank = await points.balance_rank(guild_id, interaction.user.id)

    embed = discord.Embed(
        title=f"{Emojis.COIN} Portefeuille de {interaction.user.display_name}",
        description=f"# {fmt_points(wallet.balance)}",
        color=Colors.INHOUSE,
    )
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    if wallet.notice:
        embed.add_field(name="🎁 Cadeau !", value=wallet.notice, inline=False)
    embed.add_field(
        name="⏳ Engagé dans des paris",
        value=f"{fmt_points(engaged)} sur {len(pending)} pari{'s' if len(pending) > 1 else ''}",
        inline=True,
    )
    embed.add_field(
        name=f"{Emojis.TROPHY} Classement général",
        value=f"**{rank[0]}ᵉ** sur {rank[1]}" if rank else "Non classé",
        inline=True,
    )
    _, tomorrow = day_bounds(bot.config.timezone)
    embed.add_field(
        name="☀️ Bonus quotidien",
        value=(f"{'Reçu aujourd’hui ✅' if not wallet.got_daily else 'Reçu à l’instant 🎉'}\n"
               f"Prochain : {fmt_points(settings.daily_points, signed=True)} {discord_ts(tomorrow, 'R')}"),
        inline=True,
    )
    recent = await points.recent_transactions(guild_id, interaction.user.id, limit=6)
    if recent:
        lines = [
            f"`{fmt_points(t.amount, signed=True):>10}` {KIND_LABELS.get(t.kind, t.kind)} · {discord_ts(t.created_at, 'R')}"
            for t in recent
        ]
        embed.add_field(name="🧾 Derniers mouvements", value="\n".join(lines), inline=False)
    embed.set_footer(text="Parie avec /pronos matchs • tes paris : /pronos mes-paris • règles : /pronos regles")
    await interaction.response.send_message(embed=embed, ephemeral=True)
