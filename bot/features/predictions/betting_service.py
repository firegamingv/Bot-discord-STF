"""Placer ou modifier un pari, dans une transaction SQL.

Règles (voir aussi ``/pronos regles`` et ``bot/services/betting_rules.py``) :

- paris ouverts tant que le match est « à venir » et que l'heure de début n'est pas atteinte ;
- ``winner`` pour tous les matchs, ``exact_score`` seulement en Bo3/Bo5 ;
- mise ≥ 10 et ≤ solde ;
- un pari par type et par match : parier à nouveau **modifie** le pari (l'ancienne mise est
  remboursée puis la nouvelle est prélevée, le tout atomiquement).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from bot.core.errors import NotFoundError
from bot.features.predictions.wallet_service import ensure_wallet
from bot.repositories.matches import Match, MatchRepository
from bot.repositories.points import KIND_REFUND, KIND_STAKE, PointsRepository
from bot.repositories.predictions import STATUS_PENDING, Prediction, PredictionRepository
from bot.services.betting_rules import (
    BetRuleError,
    odds_for,
    validate_choice,
    validate_stake,
)
from bot.utils.time import discord_ts

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

# Sérialise les transactions de points des pronostics (connexion SQLite unique partagée).
POINTS_LOCK = asyncio.Lock()


@dataclass(slots=True)
class BetResult:
    match: Match
    prediction: Prediction
    previous_stake: int | None   # mise remboursée si le pari a été modifié
    balance: int                 # solde après le pari
    notice: str | None           # bonus quotidien / bienvenue éventuel


def ensure_open(match: Match) -> None:
    if match.state == "cancelled":
        raise BetRuleError("Ce match a été annulé : les paris sont fermés (les mises ont été remboursées).")
    if not match.is_open():
        raise BetRuleError(
            f"Trop tard ! Les paris sur **{match.title}** sont fermés depuis le début du match "
            f"({discord_ts(match.starts_at, 'R')}). Regarde les prochains matchs avec `/pronos matchs`."
        )


async def place_bet(
    bot: "STFBot",
    guild_id: int,
    user: discord.abc.User,
    match_id: int,
    *,
    bet_type: str,
    choice: str,
    stake: int,
) -> BetResult:
    wallet = await ensure_wallet(bot, guild_id, user)
    matches = MatchRepository(bot.db)
    match = await matches.get_in_guild(guild_id, match_id)
    if match is None:
        raise NotFoundError("Ce match n'existe plus. Consulte les matchs à venir avec `/pronos matchs`.")
    ensure_open(match)
    choice = validate_choice(bet_type, choice, match.best_of)
    settings = await bot.settings.get(guild_id)
    odds = odds_for(bet_type, odds_winner=settings.odds_winner, odds_exact_score=settings.odds_exact_score)

    predictions = PredictionRepository(bot.db)
    points = PointsRepository(bot.db)
    async with POINTS_LOCK:
        # Relecture sous verrou : le match a pu commencer / le pari a pu changer entre-temps.
        match = await matches.get_in_guild(guild_id, match_id)
        if match is None:
            raise NotFoundError("Ce match n'existe plus.")
        ensure_open(match)
        existing = await predictions.get_user_bet(match.id, user.id, bet_type)
        if existing is not None and existing.status != STATUS_PENDING:
            raise BetRuleError("Ce pari est déjà réglé, il ne peut plus être modifié.")
        balance = await points.balance(guild_id, user.id)
        available = balance + (existing.stake if existing else 0)
        validate_stake(stake, available)

        tx_meta = dict(
            guild_id=guild_id, discord_id=user.id, competition_id=match.competition_id,
            tournament_name=match.tournament_name, bet_type=bet_type,
        )
        async with bot.db.transaction() as conn:
            if existing is not None:
                await PointsRepository.add_tx(
                    conn, amount=existing.stake, kind=KIND_REFUND, prediction_id=existing.id, **tx_meta
                )
                await PredictionRepository.update_pending(conn, existing.id, choice=choice, stake=stake, odds=odds)
                prediction_id = existing.id
            else:
                prediction_id = await PredictionRepository.insert(
                    conn, guild_id=guild_id, match_id=match.id, discord_id=user.id,
                    bet_type=bet_type, choice=choice, stake=stake, odds=odds,
                )
            await PointsRepository.add_tx(
                conn, amount=-stake, kind=KIND_STAKE, prediction_id=prediction_id, **tx_meta
            )

    prediction = await predictions.get(prediction_id)
    new_balance = await points.balance(guild_id, user.id)
    log.info(
        "Pari %s : membre %s, match %s, %s=%s, mise %d (cote %.2f)%s",
        prediction_id, user.id, match.id, bet_type, choice, stake, odds,
        f", modifié (ancienne mise {existing.stake})" if existing else "",
    )
    return BetResult(
        match=match,
        prediction=prediction,  # type: ignore[arg-type]
        previous_stake=existing.stake if existing else None,
        balance=new_balance,
        notice=wallet.notice,
    )
