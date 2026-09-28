"""``/pronos parier`` (vainqueur) et ``/pronos parier-score`` (score exact)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.features.predictions.autocomplete import (
    bettable_match_autocomplete,
    resolve_match,
    resolve_team,
    score_autocomplete,
    team_autocomplete,
)
from bot.features.predictions.bet_buttons import build_match_card
from bot.features.predictions.betting_service import place_bet
from bot.features.predictions.embeds import build_bet_confirmation_embed
from bot.features.predictions.group import pronos_group
from bot.services.betting_rules import BET_EXACT_SCORE, BET_WINNER, MIN_STAKE

if TYPE_CHECKING:
    from bot.core.bot import STFBot


async def _confirm(interaction: discord.Interaction, bot: "STFBot", result) -> None:  # noqa: ANN001
    embed = build_bet_confirmation_embed(
        result.match, result.prediction, balance=result.balance,
        previous_stake=result.previous_stake, notice=result.notice,
    )
    card, view = await build_match_card(bot, result.match)
    await interaction.response.send_message(embeds=[embed, card], view=view, ephemeral=True)


@pronos_group.command(name="parier", description="🏆 Parier sur le vainqueur d'un match")
@app_commands.describe(
    match="Le match (tape le nom d'une équipe)",
    equipe="L'équipe qui va gagner selon toi",
    mise=f"Nombre de points à miser (minimum {MIN_STAKE})",
)
@app_commands.autocomplete(match=bettable_match_autocomplete, equipe=team_autocomplete)
async def bet_winner(
    interaction: discord.Interaction,
    match: str,
    equipe: str,
    mise: app_commands.Range[int, MIN_STAKE, 10_000_000],
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    found = await resolve_match(bot, interaction.guild_id, match)  # type: ignore[arg-type]
    team = resolve_team(found, equipe)
    result = await place_bet(
        bot, interaction.guild_id, interaction.user, found.id,  # type: ignore[arg-type]
        bet_type=BET_WINNER, choice=team, stake=mise,
    )
    await _confirm(interaction, bot, result)


@pronos_group.command(name="parier-score", description="🎯 Parier sur le score exact d'un Bo3 / Bo5 (grosse cote !)")
@app_commands.describe(
    match="Le match (Bo3 ou Bo5)",
    score="Score final, équipe 1 - équipe 2 (ex. 2-1)",
    mise=f"Nombre de points à miser (minimum {MIN_STAKE})",
)
@app_commands.autocomplete(match=bettable_match_autocomplete, score=score_autocomplete)
async def bet_score(
    interaction: discord.Interaction,
    match: str,
    score: str,
    mise: app_commands.Range[int, MIN_STAKE, 10_000_000],
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    found = await resolve_match(bot, interaction.guild_id, match)  # type: ignore[arg-type]
    result = await place_bet(
        bot, interaction.guild_id, interaction.user, found.id,  # type: ignore[arg-type]
        bet_type=BET_EXACT_SCORE, choice=score, stake=mise,
    )
    await _confirm(interaction, bot, result)
