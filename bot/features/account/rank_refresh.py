"""Rafraîchissement des rangs Solo/Duo depuis l'API Riot.

API publique :
- ``refresh_account(bot, account, *, refresh_riot_id=False, use_cache=True)`` : met à jour un
  compte (lève ``UserFacingError`` en cas de souci — pour les commandes) ;
- ``refresh_ranks(bot, discord_ids, max_age=timedelta(hours=6))`` : met à jour en lot les
  rangs trop anciens, **ne lève jamais** (utilisé avant la génération d'équipes, profil…).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from bot.core.errors import ExternalServiceError
from bot.repositories.riot_accounts import RiotAccount, RiotAccountRepository
from bot.utils.time import now_utc

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

DEFAULT_MAX_AGE = timedelta(hours=6)
PAUSE_BETWEEN_CALLS = 0.3  # secondes, pour ménager la limite de débit Riot


def is_rank_stale(account: RiotAccount, max_age: timedelta = DEFAULT_MAX_AGE) -> bool:
    return account.rank_updated_at is None or now_utc() - account.rank_updated_at > max_age


async def refresh_account(
    bot: "STFBot",
    account: RiotAccount,
    *,
    refresh_riot_id: bool = False,
    use_cache: bool = True,
) -> RiotAccount:
    """Met à jour le rang (et éventuellement le Riot ID via le PUUID) d'un compte vérifié."""
    if account.puuid is None:
        return account
    repo = RiotAccountRepository(bot.db)
    game_name = tag_line = None
    if refresh_riot_id:
        dto = await bot.riot.get_account_by_puuid(account.puuid)
        game_name, tag_line = dto.game_name, dto.tag_line
    rank = await bot.riot.get_solo_rank(account.puuid, account.platform, use_cache=use_cache)
    await repo.update_rank(
        account.discord_id,
        tier=rank.tier if rank else None,
        division=rank.division if rank else None,
        league_points=rank.league_points if rank else None,
        game_name=game_name,
        tag_line=tag_line,
    )
    return await repo.get(account.discord_id) or account


async def refresh_ranks(
    bot: "STFBot", discord_ids: list[int], max_age: timedelta = DEFAULT_MAX_AGE
) -> None:
    """Rafraîchit (séquentiellement) les rangs vieux de plus de ``max_age``. Ne lève jamais."""
    try:
        if not bot.riot.enabled or not discord_ids:
            return
        accounts = await RiotAccountRepository(bot.db).get_many(list(dict.fromkeys(discord_ids)))
        stale = [a for a in accounts.values() if a.verified and is_rank_stale(a, max_age)]
        if not stale:
            return
        log.info("Rafraîchissement de %d rang(s) Riot", len(stale))
        for index, account in enumerate(stale):
            if index:
                await asyncio.sleep(PAUSE_BETWEEN_CALLS)
            try:
                await refresh_account(bot, account)
            except ExternalServiceError as exc:
                # API indisponible / clé expirée / limite de débit : inutile d'insister.
                log.warning("Rafraîchissement des rangs interrompu : %s", exc.message)
                return
            except Exception as exc:  # noqa: BLE001 - best effort, on continue avec les autres
                log.warning(
                    "Impossible de rafraîchir le rang de %s (%s) : %s",
                    account.riot_id, account.discord_id, getattr(exc, "message", exc),
                )
    except Exception:  # noqa: BLE001 - ne doit jamais casser l'appelant
        log.warning("Échec du rafraîchissement des rangs", exc_info=True)
