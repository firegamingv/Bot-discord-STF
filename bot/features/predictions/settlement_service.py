"""Règlement des paris d'un match terminé ou annulé.

- Idempotent : ``matches.settled`` passe à 1 dans la même transaction que les paiements
  (``UPDATE … WHERE settled = 0``) ; un second appel ne fait rien.
- Gagné → ``payout = round(mise × cote)`` (transaction ``payout``) ; perdu → 0 ;
  match annulé (ou sans vainqueur) → mise remboursée (transaction ``refund``).
- Après coup, un embed public « Résultat » est posté dans le salon des pronostics
  (si configuré et si quelqu'un avait parié).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

from bot.core.errors import NotFoundError, UserFacingError
from bot.features.predictions.betting_service import POINTS_LOCK
from bot.features.predictions.embeds import build_result_embed
from bot.repositories.competitions import CompetitionRepository
from bot.repositories.matches import STATE_CANCELLED, STATE_COMPLETED, Match, MatchRepository
from bot.repositories.points import KIND_PAYOUT, KIND_REFUND, PointsRepository
from bot.repositories.predictions import (
    STATUS_LOST,
    STATUS_PENDING,
    STATUS_REFUNDED,
    STATUS_WON,
    PredictionRepository,
)
from bot.services.betting_rules import compute_payout, is_winning_bet

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SettlementReport:
    match: Match
    cancelled: bool
    winners: list[tuple[int, int]] = field(default_factory=list)  # (discord_id, gain net)
    losers: int = 0
    refunded: int = 0
    total_paid: int = 0

    @property
    def bets(self) -> int:
        return len(self.winners) + self.losers + self.refunded


async def settle_match(bot: "STFBot", match_id: int, *, notify: bool = True) -> SettlementReport | None:
    """Règle un match ``completed`` (ou ``cancelled``). ``None`` s'il était déjà réglé."""
    matches = MatchRepository(bot.db)
    predictions = PredictionRepository(bot.db)
    async with POINTS_LOCK:
        match = await matches.get(match_id)
        if match is None:
            raise NotFoundError("Match introuvable.")
        if match.settled:
            return None
        if match.state not in (STATE_COMPLETED, STATE_CANCELLED):
            raise UserFacingError(
                "Ce match n'est pas terminé : saisis d'abord le résultat avec `/pronos-admin resultat`."
            )
        refund_all = match.state == STATE_CANCELLED or match.winner is None
        report = SettlementReport(match=match, cancelled=refund_all)
        pending = await predictions.list_for_match(match.id, status=STATUS_PENDING)

        async with bot.db.transaction() as conn:
            if not await MatchRepository.mark_settled(conn, match.id):
                return None
            for p in pending:
                meta = dict(
                    guild_id=p.guild_id, discord_id=p.discord_id, prediction_id=p.id,
                    competition_id=match.competition_id, tournament_name=match.tournament_name,
                    bet_type=p.bet_type,
                )
                if refund_all:
                    await PredictionRepository.settle(conn, p.id, status=STATUS_REFUNDED, payout=p.stake)
                    await PointsRepository.add_tx(conn, amount=p.stake, kind=KIND_REFUND, **meta)
                    report.refunded += 1
                elif is_winning_bet(p.bet_type, p.choice, winner=match.winner,
                                    team1_score=match.team1_score, team2_score=match.team2_score):
                    payout = compute_payout(p.stake, p.odds)
                    await PredictionRepository.settle(conn, p.id, status=STATUS_WON, payout=payout)
                    await PointsRepository.add_tx(conn, amount=payout, kind=KIND_PAYOUT, **meta)
                    report.winners.append((p.discord_id, payout - p.stake))
                    report.total_paid += payout
                else:
                    await PredictionRepository.settle(conn, p.id, status=STATUS_LOST, payout=0)
                    report.losers += 1

    # Un membre peut gagner sur deux types de pari : on cumule.
    merged: dict[int, int] = {}
    for uid, net in report.winners:
        merged[uid] = merged.get(uid, 0) + net
    report.winners = sorted(merged.items(), key=lambda x: x[1], reverse=True)

    log.info(
        "Match %s réglé (%s) : %d gagnant(s), %d perdant(s), %d remboursé(s), %d points distribués",
        match.id, "annulé" if refund_all else f"vainqueur {match.winner} {match.score_text or ''}",
        len(report.winners), report.losers, report.refunded, report.total_paid,
    )
    if notify and report.bets:
        await notify_result(bot, report)
    return report


async def cancel_match(bot: "STFBot", match_id: int, *, notify: bool = True) -> SettlementReport | None:
    """Annule un match et rembourse toutes les mises."""
    matches = MatchRepository(bot.db)
    match = await matches.get(match_id)
    if match is None:
        raise NotFoundError("Match introuvable.")
    if match.settled:
        raise UserFacingError("Ce match est déjà réglé : impossible de l'annuler après coup.")
    await matches.set_state(match_id, STATE_CANCELLED)
    return await settle_match(bot, match_id, notify=notify)


async def notify_result(bot: "STFBot", report: SettlementReport) -> None:
    match = report.match
    settings = await bot.settings.get(match.guild_id)
    if not settings.predictions_channel_id:
        return
    channel = bot.get_channel(settings.predictions_channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(settings.predictions_channel_id)
        except discord.HTTPException:
            log.warning("Salon des pronostics %s introuvable (serveur %s)", settings.predictions_channel_id, match.guild_id)
            return
    if not isinstance(channel, discord.abc.Messageable):
        return
    competition = await CompetitionRepository(bot.db).get(match.competition_id)
    embed = build_result_embed(
        match, competition,
        winners=report.winners, losers_count=report.losers, refunded_count=report.refunded,
        total_paid=report.total_paid, cancelled=report.cancelled,
    )
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        log.warning("Impossible de publier le résultat du match %s dans %s", match.id, channel, exc_info=True)
