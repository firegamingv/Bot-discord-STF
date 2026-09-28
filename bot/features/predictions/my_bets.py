"""``/pronos mes-paris [statut]`` : paris en cours et historique récent (éphémère)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.features.predictions.embeds import fmt_points, format_prediction_line
from bot.features.predictions.group import pronos_group
from bot.features.predictions.wallet_service import ensure_wallet
from bot.repositories.matches import MatchRepository
from bot.repositories.predictions import (
    STATUS_LOST,
    STATUS_PENDING,
    STATUS_REFUNDED,
    STATUS_WON,
    PredictionRepository,
)
from bot.utils.embeds import Colors, chunk_lines

if TYPE_CHECKING:
    from bot.core.bot import STFBot


@pronos_group.command(name="mes-paris", description="🎟️ Voir tes paris en cours et ton historique")
@app_commands.describe(statut="Quels paris afficher (par défaut : en cours + 10 derniers réglés)")
@app_commands.choices(statut=[
    app_commands.Choice(name="⏳ En cours", value="pending"),
    app_commands.Choice(name="🏁 Terminés", value="settled"),
    app_commands.Choice(name="📜 Tous", value="all"),
])
async def my_bets(interaction: discord.Interaction, statut: app_commands.Choice[str] | None = None) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild_id: int = interaction.guild_id  # type: ignore[assignment]
    wallet = await ensure_wallet(bot, guild_id, interaction.user)
    repo = PredictionRepository(bot.db)
    mode = statut.value if statut else "all"

    pending = await repo.list_for_user(guild_id, interaction.user.id, statuses=(STATUS_PENDING,), limit=25) \
        if mode in ("pending", "all") else []
    settled = await repo.list_for_user(
        guild_id, interaction.user.id, statuses=(STATUS_WON, STATUS_LOST, STATUS_REFUNDED),
        limit=10 if mode == "all" else 20,
    ) if mode in ("settled", "all") else []

    matches = MatchRepository(bot.db)
    cache: dict[int, object] = {}

    async def lines_for(preds):  # noqa: ANN001, ANN202
        out = []
        for p in preds:
            if p.match_id not in cache:
                cache[p.match_id] = await matches.get(p.match_id)
            out.append(format_prediction_line(p, cache[p.match_id]))  # type: ignore[arg-type]
        return out

    embed = discord.Embed(title="🎟️ Mes paris", color=Colors.ESPORT)
    if wallet.notice:
        embed.description = wallet.notice
    engaged = sum(p.stake for p in pending)
    if mode in ("pending", "all"):
        if pending:
            for i, chunk in enumerate(chunk_lines(await lines_for(pending))[:2]):
                embed.add_field(name=f"⏳ En cours ({len(pending)})" if i == 0 else "​", value=chunk, inline=False)
        else:
            embed.add_field(name="⏳ En cours", value="Aucun pari en cours. Les matchs à venir : `/pronos matchs` 🎲", inline=False)
    if mode in ("settled", "all"):
        if settled:
            for i, chunk in enumerate(chunk_lines(await lines_for(settled))[:2]):
                embed.add_field(name="🏁 Derniers résultats" if i == 0 else "​", value=chunk, inline=False)
        else:
            embed.add_field(name="🏁 Derniers résultats", value="Aucun pari réglé pour l'instant.", inline=False)
    embed.set_footer(text=f"💼 Solde : {wallet.balance} 🪙 • engagé : {engaged} 🪙 • stats complètes : /pronos stats")
    embed.add_field(name="💼 Solde disponible", value=fmt_points(wallet.balance), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)
