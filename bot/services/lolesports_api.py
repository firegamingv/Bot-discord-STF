"""Client de l'API (non officielle) LoL Esports : ligues, planning des matchs, tournois.

API utilisée par le site lolesports.com :
``https://esports-api.lolesports.com/persisted/gw/<endpoint>?hl=fr-FR`` avec l'en-tête
``x-api-key``. Elle n'est pas documentée officiellement ; ce module est donc volontairement
défensif :

- délai maximum par requête, toute erreur réseau / HTTP → ``ExternalServiceError`` ;
- un élément au format inattendu est journalisé puis ignoré (on ne jette pas toute la page).

Les fonctions ``parse_*`` sont pures (sans réseau) pour pouvoir être testées.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp

from bot.core.errors import ExternalServiceError

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://esports-api.lolesports.com/persisted/gw"
DEFAULT_LOCALE = "fr-FR"

# États renvoyés par l'API pour un match
STATE_UNSTARTED = "unstarted"
STATE_IN_PROGRESS = "inProgress"
STATE_COMPLETED = "completed"
_KNOWN_STATES = {STATE_UNSTARTED, STATE_IN_PROGRESS, STATE_COMPLETED}

# Mots de slug affichés en majuscules dans les noms de tournois (« lec_summer_2024 » → « LEC Summer 2024 »)
_ACRONYMS = {
    "lec", "lck", "lpl", "lcs", "lta", "lcp", "msi", "lfl", "vcs", "pcs", "ljl", "lla",
    "cblol", "tcl", "nlc", "emea", "na", "eu", "euw", "kr", "cl", "prm", "ewc", "ase", "lco",
}


@dataclass(frozen=True, slots=True)
class LeagueDTO:
    id: str
    slug: str
    name: str
    region: str
    image: str | None
    priority: int = 1000  # ordre d'affichage suggéré par l'API (plus petit = plus important)


@dataclass(frozen=True, slots=True)
class MatchDTO:
    external_id: str
    league_id: str | None      # renseigné si déductible (une seule ligue demandée)
    league_slug: str | None
    league_name: str
    block_name: str | None     # « Semaine 3 », « Playoffs »…
    team1_name: str
    team1_code: str | None
    team2_name: str
    team2_code: str | None
    best_of: int
    starts_at: datetime        # UTC
    state: str                 # unstarted | inProgress | completed
    team1_wins: int | None
    team2_wins: int | None
    winner: int | None         # 1 | 2 | None

    @property
    def tournament_name(self) -> str | None:
        """Nom de « bloc » (ex. « Playoffs ») ; le nom de tournoi précis vient de ``get_tournaments``."""
        return self.block_name


@dataclass(frozen=True, slots=True)
class ScheduleDTO:
    events: list[MatchDTO]
    older_token: str | None
    newer_token: str | None


@dataclass(frozen=True, slots=True)
class TournamentDTO:
    id: str
    slug: str
    name: str
    start_date: datetime | None  # UTC, début de journée
    end_date: datetime | None    # UTC, fin de journée incluse

    def contains(self, dt: datetime) -> bool:
        if self.start_date is None or self.end_date is None:
            return False
        return self.start_date <= dt <= self.end_date


# ---------------------------------------------------------------------------- parsing (pur)
def _parse_datetime(raw: Any) -> datetime:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"date absente : {raw!r}")
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _opt_int(raw: Any) -> int | None:
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def pretty_tournament_name(slug: str) -> str:
    """``lec_summer_2024`` → ``LEC Summer 2024`` ; ``worlds_2025`` → ``Worlds 2025``."""
    words = [w for w in slug.replace("-", "_").split("_") if w]
    out = []
    for w in words:
        if w.lower() in _ACRONYMS:
            out.append(w.upper())
        elif w.isdigit():
            out.append(w)
        else:
            out.append(w.capitalize())
    return " ".join(out) or slug


def parse_leagues(payload: Any) -> list[LeagueDTO]:
    leagues_raw = ((payload or {}).get("data") or {}).get("leagues")
    if not isinstance(leagues_raw, list):
        log.warning("Réponse getLeagues inattendue : %.200r", payload)
        return []
    result: list[LeagueDTO] = []
    for item in leagues_raw:
        try:
            display = item.get("displayPriority") or {}
            priority = _opt_int(display.get("position")) if isinstance(display, dict) else None
            if priority is None:
                priority = _opt_int(item.get("priority"))
            result.append(
                LeagueDTO(
                    id=str(item["id"]),
                    slug=str(item.get("slug") or item["id"]),
                    name=str(item["name"]),
                    region=str(item.get("region") or ""),
                    image=item.get("image") or None,
                    priority=priority if priority is not None else 1000,
                )
            )
        except (KeyError, TypeError, AttributeError):
            log.warning("Ligue LoL Esports ignorée (format inattendu) : %.200r", item)
    return result


def parse_match_event(event: Any, *, league_id: str | None = None) -> MatchDTO | None:
    """Convertit un élément ``events[]`` de getSchedule. ``None`` si ce n'est pas un match
    (émission, cérémonie…) ou si le format est inattendu (journalisé)."""
    try:
        if event.get("type") != "match":
            return None
        match = event["match"]
        teams = match["teams"]
        if not isinstance(teams, list) or len(teams) != 2:
            raise ValueError("il faut exactement deux équipes")
        state = event.get("state") or STATE_UNSTARTED
        if state not in _KNOWN_STATES:
            log.debug("État de match inconnu %r, traité comme 'unstarted'", state)
            state = STATE_UNSTARTED
        strategy = match.get("strategy") or {}
        best_of = _opt_int(strategy.get("count")) or 1

        wins: list[int | None] = []
        winner: int | None = None
        for idx, team in enumerate(teams, start=1):
            result = team.get("result") or {}
            wins.append(_opt_int(result.get("gameWins")))
            if result.get("outcome") == "win":
                winner = idx
        league = event.get("league") or {}
        return MatchDTO(
            external_id=str(match["id"]),
            league_id=league_id or (str(league["id"]) if league.get("id") else None),
            league_slug=league.get("slug"),
            league_name=str(league.get("name") or ""),
            block_name=event.get("blockName") or None,
            team1_name=str(teams[0].get("name") or "TBD"),
            team1_code=teams[0].get("code") or None,
            team2_name=str(teams[1].get("name") or "TBD"),
            team2_code=teams[1].get("code") or None,
            best_of=best_of,
            starts_at=_parse_datetime(event.get("startTime")),
            state=state,
            team1_wins=wins[0],
            team2_wins=wins[1],
            winner=winner if state == STATE_COMPLETED else None,
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        log.warning("Événement LoL Esports ignoré (%s) : %.300r", exc, event)
        return None


def parse_schedule(payload: Any, *, league_ids: list[str] | None = None) -> ScheduleDTO:
    schedule = ((payload or {}).get("data") or {}).get("schedule")
    if not isinstance(schedule, dict):
        log.warning("Réponse getSchedule inattendue : %.200r", payload)
        return ScheduleDTO(events=[], older_token=None, newer_token=None)
    single_league = league_ids[0] if league_ids and len(league_ids) == 1 else None
    events: list[MatchDTO] = []
    for raw in schedule.get("events") or []:
        dto = parse_match_event(raw, league_id=single_league)
        if dto is not None:
            events.append(dto)
    pages = schedule.get("pages") or {}
    return ScheduleDTO(
        events=events,
        older_token=pages.get("older") or None,
        newer_token=pages.get("newer") or None,
    )


def parse_tournaments(payload: Any) -> list[TournamentDTO]:
    leagues = ((payload or {}).get("data") or {}).get("leagues")
    if not isinstance(leagues, list):
        log.warning("Réponse getTournamentsForLeague inattendue : %.200r", payload)
        return []
    result: list[TournamentDTO] = []
    for league in leagues:
        for item in (league or {}).get("tournaments") or []:
            try:
                slug = str(item.get("slug") or item["id"])
                start = _parse_datetime(item["startDate"] + "T00:00:00Z") if item.get("startDate") else None
                end = _parse_datetime(item["endDate"] + "T23:59:59Z") if item.get("endDate") else None
                result.append(
                    TournamentDTO(id=str(item["id"]), slug=slug, name=pretty_tournament_name(slug),
                                  start_date=start, end_date=end)
                )
            except (KeyError, TypeError, ValueError, AttributeError):
                log.warning("Tournoi LoL Esports ignoré (format inattendu) : %.200r", item)
    return result


# ---------------------------------------------------------------------------- client HTTP
class LolEsportsClient:
    """Client asynchrone. Constructeur : ``LolEsportsClient(session, *, api_key)``."""

    def __init__(
        self,
        session: aiohttp.ClientSession | None,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        locale: str = DEFAULT_LOCALE,
        timeout: float = 10.0,
        tournaments_cache_ttl: float = 6 * 3600,
    ) -> None:
        self.session = session
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.locale = locale
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._tournaments_ttl = tournaments_cache_ttl
        self._tournaments_cache: dict[str, tuple[float, list[TournamentDTO]]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and self.session is not None

    async def _get(self, endpoint: str, params: dict[str, str] | None = None) -> Any:
        if self.session is None:
            raise ExternalServiceError("Le client LoL Esports n'est pas initialisé. Réessaie dans un instant.")
        query = {"hl": self.locale, **(params or {})}
        url = f"{self.base_url}/{endpoint}"
        try:
            async with self.session.get(
                url, params=query, headers={"x-api-key": self.api_key}, timeout=self.timeout
            ) as resp:
                if resp.status == 403 or resp.status == 401:
                    log.error("LoL Esports a refusé la clé API (%s) sur %s", resp.status, endpoint)
                    raise ExternalServiceError(
                        "L'API LoL Esports refuse notre clé. Un administrateur doit vérifier "
                        "`LOLESPORTS_API_KEY` dans le fichier .env."
                    )
                if resp.status >= 400:
                    body = (await resp.text())[:200]
                    log.warning("LoL Esports %s → HTTP %s : %s", endpoint, resp.status, body)
                    raise ExternalServiceError(
                        f"L'API LoL Esports a répondu une erreur (HTTP {resp.status}). "
                        "Réessaie dans quelques minutes."
                    )
                return await resp.json(content_type=None)
        except ExternalServiceError:
            raise
        except asyncio.TimeoutError as exc:
            log.warning("LoL Esports %s : délai dépassé", endpoint)
            raise ExternalServiceError(
                "L'API LoL Esports met trop de temps à répondre. Réessaie dans quelques minutes."
            ) from exc
        except (aiohttp.ClientError, ValueError) as exc:
            log.warning("LoL Esports %s : %s", endpoint, exc)
            raise ExternalServiceError(
                "Impossible de joindre l'API LoL Esports pour le moment. Réessaie plus tard."
            ) from exc

    async def get_leagues(self) -> list[LeagueDTO]:
        return parse_leagues(await self._get("getLeagues"))

    async def get_schedule(self, league_ids: list[str], page_token: str | None = None) -> ScheduleDTO:
        params: dict[str, str] = {}
        if league_ids:
            params["leagueId"] = ",".join(league_ids)
        if page_token:
            params["pageToken"] = page_token
        return parse_schedule(await self._get("getSchedule", params), league_ids=league_ids)

    async def get_tournaments(self, league_id: str) -> list[TournamentDTO]:
        """Tournois d'une ligue (ex. « LEC Summer 2026 »), mis en cache quelques heures."""
        cached = self._tournaments_cache.get(league_id)
        if cached and time.monotonic() - cached[0] < self._tournaments_ttl:
            return cached[1]
        tournaments = parse_tournaments(
            await self._get("getTournamentsForLeague", {"leagueId": league_id})
        )
        self._tournaments_cache[league_id] = (time.monotonic(), tournaments)
        return tournaments
