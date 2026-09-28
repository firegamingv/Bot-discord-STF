"""Boutons persistants de pari sous chaque carte de match : ``bet:<match_id>:<choice>``.

- ``choice = 1`` / ``2`` : pari « vainqueur » sur l'équipe 1 / 2 → modale de mise ;
- ``choice = score``     : pari « score exact » (Bo3/Bo5) → menu éphémère des scores
  possibles, puis modale de mise.

Les boutons survivent aux redémarrages (``DynamicItem`` enregistré dans ``__init__.py``).
Après un pari depuis un message public, la carte du match est rafraîchie (répartition des paris).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from bot.core.error_reporting import BaseModal, BaseView, interaction_guard
from bot.core.errors import NotFoundError, UserFacingError
from bot.features.predictions.betting_service import ensure_open, place_bet
from bot.features.predictions.embeds import (
    build_bet_confirmation_embed,
    build_match_embed,
    fmt_points,
)
from bot.repositories.competitions import CompetitionRepository
from bot.repositories.matches import Match, MatchRepository
from bot.repositories.points import PointsRepository
from bot.repositories.predictions import PredictionRepository
from bot.services.betting_rules import (
    BET_EXACT_SCORE,
    BET_WINNER,
    DEFAULT_STAKE,
    MIN_STAKE,
    BetRuleError,
    exact_score_allowed,
    possible_scores,
)
from bot.utils import embeds

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------- carte de match
def build_match_view(match: Match) -> discord.ui.View:
    """Boutons de pari (persistants) d'un match ; grisés si les paris sont fermés."""
    view = discord.ui.View(timeout=None)
    closed = not match.is_open()
    view.add_item(BetButton(match.id, "1", label=match.team_code(1)[:80], emoji="🔵",
                            style=discord.ButtonStyle.primary, disabled=closed))
    view.add_item(BetButton(match.id, "2", label=match.team_code(2)[:80], emoji="🔴",
                            style=discord.ButtonStyle.danger, disabled=closed))
    if exact_score_allowed(match.best_of):
        view.add_item(BetButton(match.id, "score", label="Score exact", emoji="🎯",
                                style=discord.ButtonStyle.secondary, disabled=closed))
    return view


async def build_match_card(bot: "STFBot", match: Match) -> tuple[discord.Embed, discord.ui.View]:
    settings = await bot.settings.get(match.guild_id)
    competition = await CompetitionRepository(bot.db).get(match.competition_id)
    counts = await PredictionRepository(bot.db).count_by_choice(match.id)
    embed = build_match_embed(
        match, competition, counts,
        odds_winner=settings.odds_winner, odds_exact_score=settings.odds_exact_score,
    )
    return embed, build_match_view(match)


async def refresh_public_card(bot: "STFBot", message: discord.Message | None, match_id: int) -> None:
    """Met à jour la carte publique d'où vient le pari (compteurs de paris)."""
    if message is None or message.flags.ephemeral:
        return
    match = await MatchRepository(bot.db).get(match_id)
    if match is None:
        return
    embed, view = await build_match_card(bot, match)
    try:
        await message.edit(embed=embed, view=view)
    except discord.HTTPException:
        log.debug("Impossible de rafraîchir la carte du match %s", match_id, exc_info=True)


# ---------------------------------------------------------------------------- modale de mise
class StakeModal(BaseModal):
    def __init__(
        self,
        match: Match,
        *,
        bet_type: str,
        choice: str,
        default_stake: int,
        balance: int,
        origin: discord.Message | None,
    ) -> None:
        if bet_type == BET_WINNER:
            title = f"🏆 Victoire de {match.team_code(choice)}"
        else:
            title = f"🎯 Score {match.team_code(1)} {choice} {match.team_code(2)}"
        super().__init__(title=title[:45], timeout=300)
        self.match = match
        self.bet_type = bet_type
        self.choice = choice
        self.origin = origin
        self.amount = discord.ui.TextInput(
            label="Combien de points veux-tu miser ?",
            default=str(default_stake),
            placeholder=f"Minimum {MIN_STAKE} • ton solde : {balance}",
            min_length=1,
            max_length=9,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.amount.value.strip().replace(" ", "").replace(" ", "")
        if not raw.isdigit():
            raise BetRuleError(f"« {self.amount.value} » n'est pas un nombre de points valide. Exemple : `50`.")
        bot: STFBot = interaction.client  # type: ignore[assignment]
        result = await place_bet(
            bot, interaction.guild_id, interaction.user, self.match.id,  # type: ignore[arg-type]
            bet_type=self.bet_type, choice=self.choice, stake=int(raw),
        )
        embed = build_bet_confirmation_embed(
            result.match, result.prediction, balance=result.balance,
            previous_stake=result.previous_stake, notice=result.notice,
        )
        await embeds.reply(interaction, embed, ephemeral=True)
        await refresh_public_card(bot, self.origin, self.match.id)


async def open_stake_modal(
    interaction: discord.Interaction, match: Match, *, bet_type: str, choice: str,
    origin: discord.Message | None,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    existing = await PredictionRepository(bot.db).get_user_bet(match.id, interaction.user.id, bet_type)
    balance = await PointsRepository(bot.db).balance(match.guild_id, interaction.user.id)
    default = existing.stake if existing and existing.status == "pending" else DEFAULT_STAKE
    await interaction.response.send_modal(
        StakeModal(match, bet_type=bet_type, choice=choice, default_stake=default,
                   balance=balance, origin=origin)
    )


# ---------------------------------------------------------------------------- choix du score
class ScoreSelect(discord.ui.Select):
    def __init__(self, match: Match, current: str | None) -> None:
        options = []
        for score in possible_scores(match.best_of):
            a, b = score.split("-")
            winner = match.team_code(1) if int(a) > int(b) else match.team_code(2)
            options.append(discord.SelectOption(
                label=f"{match.team_code(1)} {a} - {b} {match.team_code(2)}"[:100],
                value=score,
                description=f"Victoire de {winner}"[:100],
                emoji="🔵" if int(a) > int(b) else "🔴",
                default=score == current,
            ))
        super().__init__(placeholder="Choisis le score final…", options=options, min_values=1, max_values=1)
        self.match = match

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ScoreSelectView = self.view  # type: ignore[assignment]
        bot: STFBot = interaction.client  # type: ignore[assignment]
        match = await MatchRepository(bot.db).get(self.match.id)
        if match is None:
            raise NotFoundError("Ce match n'existe plus.")
        ensure_open(match)
        await open_stake_modal(interaction, match, bet_type=BET_EXACT_SCORE, choice=self.values[0], origin=view.origin)


class ScoreSelectView(BaseView):
    def __init__(self, match: Match, *, current: str | None, origin: discord.Message | None) -> None:
        super().__init__(timeout=300)
        self.origin = origin
        self.add_item(ScoreSelect(match, current))


# ---------------------------------------------------------------------------- bouton persistant
class BetButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"bet:(?P<match_id>\d+):(?P<choice>1|2|score)",
):
    def __init__(
        self,
        match_id: int,
        choice: str,
        *,
        label: str | None = None,
        emoji: str | None = None,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label or ("Score exact" if choice == "score" else f"Équipe {choice}"),
                emoji=emoji,
                style=style,
                disabled=disabled,
                custom_id=f"bet:{match_id}:{choice}",
            )
        )
        self.match_id = match_id
        self.choice = choice

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):  # noqa: ANN001
        return cls(int(match["match_id"]), match["choice"])

    async def callback(self, interaction: discord.Interaction) -> None:
        async with interaction_guard(interaction):
            await self._handle(interaction)

    async def _handle(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            raise UserFacingError("Les paris se font sur un serveur.")
        bot: STFBot = interaction.client  # type: ignore[assignment]
        match = await MatchRepository(bot.db).get_in_guild(interaction.guild_id, self.match_id)
        if match is None:
            raise NotFoundError("Ce match n'existe plus. Consulte les matchs à venir avec `/pronos matchs`.")
        ensure_open(match)
        origin = interaction.message
        if self.choice in ("1", "2"):
            await open_stake_modal(interaction, match, bet_type=BET_WINNER, choice=self.choice, origin=origin)
            return
        if not exact_score_allowed(match.best_of):
            raise BetRuleError(f"Pas de pari sur le score exact pour un Bo{match.best_of}.")
        existing = await PredictionRepository(bot.db).get_user_bet(match.id, interaction.user.id, BET_EXACT_SCORE)
        current = existing.choice if existing and existing.status == "pending" else None
        text = f"🎯 **{match.title}** (Bo{match.best_of}) — quel sera le score final ?"
        if current:
            text += f"\nTon pari actuel : **{current}** ({fmt_points(existing.stake)}) — tu peux le modifier."  # type: ignore[union-attr]
        await interaction.response.send_message(
            text, view=ScoreSelectView(match, current=current, origin=origin), ephemeral=True
        )
