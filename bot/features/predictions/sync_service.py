"""Synchronisation des matchs LoL Esports vers la base (utilisée par la tâche et la commande).

Pour chaque serveur, pour chaque compétition suivie (source ``lolesports``) :

1. récupère le planning (fenêtre : hier → +7 jours), en suivant la pagination de l'API ;
2. crée / met à jour les matchs (équipes, horaire, état, score) ;
3. dès qu'un match est terminé avec un vainqueur, règle les paris (``settlement_service``).

Un seul appel d'API par lot de ligues, partagé entre tous les serveurs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from bot.core.errors import ExternalServiceError
from bot.features.predictions.settlement_service import settle_match
from bot.repositories.competitions import SOURCE_LOLESPORTS, Competition, CompetitionRepository
from bot.repositories.matches import API_STATE_MAP, STATE_COMPLETED, STATE_LIVE, MatchRepository
from bot.services.lolesports_api import LolEsportsClient, MatchDTO, TournamentDTO
from bot.services.periods import day_bounds
from bot.utils.time import now_utc

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

FUTURE_DAYS = 7
MAX_NEWER_PAGES = 4
MAX_OLDER_PAGES = 2


@dataclass(slots=True)
class SyncReport:
    competitions: int = 0
    created: int = 0
    updated: int = 0
    settled: int = 0
    errors: list[str] = field(default_factory=list)


async def fetch_window(
    client: LolEsportsClient, league_ids: list[str], start: datetime, end: datetime
) -> list[MatchDTO]:
    """Matchs des ligues dont le début est dans ``[start, end[`` (pagination incluse)."""
    first = await client.get_schedule(league_ids)
    events: dict[str, MatchDTO] = {e.external_id: e for e in first.events}

    token, pages = first.newer_token, 0
    while token and pages < MAX_NEWER_PAGES and (not events or max(e.starts_at for e in events.values()) < end):
        page = await client.get_schedule(league_ids, token)
        events.update({e.external_id: e for e in page.events})
        token, pages = page.newer_token, pages + 1

    token, pages = first.older_token, 0
    while token and pages < MAX_OLDER_PAGES and (not events or min(e.starts_at for e in events.values()) > start):
        page = await client.get_schedule(league_ids, token)
        events.update({e.external_id: e for e in page.events})
        token, pages = page.older_token, pages + 1

    return sorted((e for e in events.values() if start <= e.starts_at < end), key=lambda e: e.starts_at)


def _competition_for(dto: MatchDTO, comps: list[Competition]) -> Competition | None:
    for c in comps:
        if dto.league_id and c.external_id == dto.league_id:
            return c
    for c in comps:
        if dto.league_slug and c.slug and c.slug == dto.league_slug:
            return c
    for c in comps:
        if dto.league_name and c.name.lower() == dto.league_name.lower():
            return c
    return None


def _tournament_name(dto: MatchDTO, tournaments: list[TournamentDTO], fallback_league: str) -> str:
    for t in tournaments:
        if t.contains(dto.starts_at):
            return t.name
    return f"{dto.league_name or fallback_league} {dto.starts_at.year}"


def db_state(dto: MatchDTO) -> str:
    """État en base. Un match « terminé » sans vainqueur connu reste « en cours » (pas de
    règlement tant que l'API n'a pas publié le résultat)."""
    state = API_STATE_MAP.get(dto.state, "upcoming")
    if state == STATE_COMPLETED and dto.winner is None:
        return STATE_LIVE
    return state


async def sync_competitions(bot: "STFBot", competitions: list[Competition]) -> SyncReport:
    report = SyncReport(competitions=len(competitions))
    comps = [c for c in competitions if c.source == SOURCE_LOLESPORTS and c.followed]
    if not comps:
        return report
    client = bot.esports
    start = day_bounds(bot.config.timezone, offset_days=-1)[0]
    end = now_utc() + timedelta(days=FUTURE_DAYS)
    league_ids = sorted({c.external_id for c in comps})

    try:
        dtos = await fetch_window(client, league_ids, start, end)
    except ExternalServiceError as exc:
        report.errors.append(exc.message)
        return report

    tournaments: dict[str, list[TournamentDTO]] = {}
    for league_id in league_ids:
        try:
            tournaments[league_id] = await client.get_tournaments(league_id)
        except ExternalServiceError:
            tournaments[league_id] = []

    matches = MatchRepository(bot.db)
    to_settle: list[int] = []
    by_guild: dict[int, list[Competition]] = {}
    for c in comps:
        by_guild.setdefault(c.guild_id, []).append(c)

    for guild_id, guild_comps in by_guild.items():
        for dto in dtos:
            comp = _competition_for(dto, guild_comps)
            if comp is None:
                continue
            state = db_state(dto)
            match, previous = await matches.upsert_external(
                guild_id, comp.id,
                external_id=dto.external_id,
                tournament_name=_tournament_name(dto, tournaments.get(comp.external_id, []), comp.name),
                block_name=dto.block_name,
                team1_name=dto.team1_name, team1_code=dto.team1_code,
                team2_name=dto.team2_name, team2_code=dto.team2_code,
                best_of=dto.best_of, starts_at=dto.starts_at, state=state,
                team1_score=dto.team1_wins, team2_score=dto.team2_wins,
                winner=dto.winner if state == STATE_COMPLETED else None,
            )
            if previous is None:
                report.created += 1
            elif previous.state != match.state or previous.score_text != match.score_text \
                    or previous.starts_at != match.starts_at:
                report.updated += 1
            if match.state == STATE_COMPLETED and not match.settled:
                to_settle.append(match.id)

    # Rattrapage : matchs terminés mais non réglés (ex. crash pendant un règlement)
    guilds = set(by_guild)
    for m in await matches.list_to_settle():
        if m.guild_id in guilds and m.id not in to_settle:
            to_settle.append(m.id)

    for match_id in to_settle:
        try:
            if await settle_match(bot, match_id):
                report.settled += 1
        except Exception:  # noqa: BLE001 - un match problématique ne bloque pas les autres
            log.exception("Échec du règlement du match %s", match_id)
            report.errors.append(f"règlement du match #{match_id} impossible")

    log.info(
        "Synchro LoL Esports : %d compétition(s), %d créé(s), %d mis à jour, %d réglé(s)",
        len(comps), report.created, report.updated, report.settled,
    )
    return report


async def sync_all(bot: "STFBot") -> SyncReport:
    comps = await CompetitionRepository(bot.db).list_followed_all_guilds(SOURCE_LOLESPORTS)
    comps = [c for c in comps if bot.get_guild(c.guild_id) is not None]
    return await sync_competitions(bot, comps)


async def sync_guild(bot: "STFBot", guild_id: int) -> SyncReport:
    comps = await CompetitionRepository(bot.db).list(guild_id, followed_only=True, source=SOURCE_LOLESPORTS)
    return await sync_competitions(bot, comps)
