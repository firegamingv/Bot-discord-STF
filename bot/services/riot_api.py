"""Client minimal de l'API Riot Games (comptes + classement Solo/Duo).

Aucune dépendance à Discord : ce module est testable seul (voir ``tests/test_riot_api.py``).

Endpoints utilisés :

- account-v1 (routage **régional**, ex. ``europe``) :
  ``/riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}`` et ``/by-puuid/{puuid}``
- league-v4 (routage **plateforme**, ex. ``euw1``) :
  ``/lol/league/v4/entries/by-puuid/{puuid}``

Gestion des erreurs (toutes les exceptions levées sont des ``UserFacingError`` : leur
message peut être affiché tel quel à l'utilisateur) :

- 404            -> ``NotFoundError`` (compte introuvable)
- 401 / 403      -> ``ExternalServiceError`` (clé invalide/expirée) + log ERROR
- 429            -> on respecte ``Retry-After`` (1 nouvel essai max, attente <= 10 s)
- 5xx / timeout / erreur réseau -> ``ExternalServiceError``
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import aiohttp

from bot.core.errors import ExternalServiceError, NotFoundError

log = logging.getLogger(__name__)

SOLO_QUEUE = "RANKED_SOLO_5x5"
RANK_CACHE_TTL = 300.0  # secondes
MAX_RETRY_WAIT = 10.0  # secondes
_MISSING_KEY_MESSAGE = (
    "La liaison avec l'API Riot n'est pas configurée sur ce bot (clé `RIOT_API_KEY` absente). "
    "Préviens un admin pour qu'il ajoute une clé dans le fichier `.env`."
)


@dataclass(frozen=True, slots=True)
class RiotAccountDTO:
    puuid: str
    game_name: str
    tag_line: str

    @property
    def riot_id(self) -> str:
        return f"{self.game_name}#{self.tag_line}"


@dataclass(frozen=True, slots=True)
class RankDTO:
    tier: str  # ex. GOLD
    division: str | None  # ex. II (None pour Maître et plus)
    league_points: int
    wins: int
    losses: int

    @property
    def games(self) -> int:
        return self.wins + self.losses

    @property
    def winrate(self) -> float | None:
        """Pourcentage de victoires (0-100), None si aucune partie."""
        return None if self.games == 0 else 100 * self.wins / self.games


class RiotClient:
    """Client asynchrone de l'API Riot.

    ``base_url`` permet de rediriger toutes les requêtes vers un autre serveur (tests) ;
    par défaut ``https://{hôte}.api.riotgames.com`` où l'hôte est la région ou la plateforme.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        api_key: str | None,
        region: str,
        platform: str,
        base_url: str | None = None,
        rank_cache_ttl: float = RANK_CACHE_TTL,
        max_retry_wait: float = MAX_RETRY_WAIT,
    ) -> None:
        self.session = session
        self.api_key = (api_key or "").strip() or None
        self.region = region.lower()
        self.platform = platform.lower()
        self.base_url = base_url.rstrip("/") if base_url else None
        self.rank_cache_ttl = rank_cache_ttl
        self.max_retry_wait = max_retry_wait
        # (plateforme, puuid) -> (expiration monotonic, rang ou None si non classé)
        self._rank_cache: dict[tuple[str, str], tuple[float, RankDTO | None]] = {}

    # ------------------------------------------------------------------ état
    @property
    def enabled(self) -> bool:
        """True si une clé API est configurée."""
        return self.api_key is not None

    def clear_cache(self) -> None:
        self._rank_cache.clear()

    def invalidate_rank(self, puuid: str, platform: str | None = None) -> None:
        self._rank_cache.pop(((platform or self.platform).lower(), puuid), None)

    # ------------------------------------------------------------------ endpoints publics
    async def get_account_by_riot_id(self, game_name: str, tag_line: str) -> RiotAccountDTO:
        game_name, tag_line = game_name.strip(), tag_line.strip().lstrip("#")
        path = (
            "/riot/account/v1/accounts/by-riot-id/"
            f"{quote(game_name, safe='')}/{quote(tag_line, safe='')}"
        )
        data = await self._get(
            self.region,
            path,
            not_found=(
                f"Aucun compte Riot ne correspond à **{game_name}#{tag_line}**. Vérifie l'orthographe "
                "(le Riot ID est visible en haut du client LoL, ex. `Pseudo#EUW`)."
            ),
        )
        return self._account_from(data, fallback=(game_name, tag_line))

    async def get_account_by_puuid(self, puuid: str) -> RiotAccountDTO:
        data = await self._get(
            self.region,
            f"/riot/account/v1/accounts/by-puuid/{quote(puuid, safe='')}",
            not_found=(
                "Ce compte Riot n'existe plus (ou a été transféré). "
                "Relie ton compte avec `/compte lier`."
            ),
        )
        return self._account_from(data)

    async def get_solo_rank(
        self, puuid: str, platform: str | None = None, *, use_cache: bool = True
    ) -> RankDTO | None:
        """Rang Solo/Duo du joueur, ou ``None`` s'il n'est pas classé cette saison."""
        platform = (platform or self.platform).lower()
        key = (platform, puuid)
        now = time.monotonic()
        if use_cache and (cached := self._rank_cache.get(key)) and cached[0] > now:
            return cached[1]

        data = await self._get(
            platform,
            f"/lol/league/v4/entries/by-puuid/{quote(puuid, safe='')}",
            not_found=(
                "Impossible de trouver le classement de ce compte sur le serveur "
                f"`{platform}`. Le compte est peut-être sur un autre serveur."
            ),
        )
        rank: RankDTO | None = None
        for entry in data or []:
            if entry.get("queueType") == SOLO_QUEUE:
                tier = str(entry.get("tier", "")).upper()
                division = entry.get("rank")
                if tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
                    division = None
                rank = RankDTO(
                    tier=tier,
                    division=division,
                    league_points=int(entry.get("leaguePoints", 0)),
                    wins=int(entry.get("wins", 0)),
                    losses=int(entry.get("losses", 0)),
                )
                break
        self._rank_cache[key] = (time.monotonic() + self.rank_cache_ttl, rank)
        return rank

    # ------------------------------------------------------------------ interne
    @staticmethod
    def _account_from(data: Any, fallback: tuple[str, str] | None = None) -> RiotAccountDTO:
        if not isinstance(data, dict) or not data.get("puuid"):
            raise ExternalServiceError(
                "L'API Riot a renvoyé une réponse inattendue. Réessaie dans quelques minutes."
            )
        game_name = data.get("gameName") or (fallback[0] if fallback else "")
        tag_line = data.get("tagLine") or (fallback[1] if fallback else "")
        return RiotAccountDTO(puuid=data["puuid"], game_name=game_name, tag_line=tag_line)

    def _url(self, host: str, path: str) -> str:
        if self.base_url:
            return f"{self.base_url}{path}"
        return f"https://{host}.api.riotgames.com{path}"

    async def _get(self, host: str, path: str, *, not_found: str) -> Any:
        if not self.enabled:
            raise ExternalServiceError(_MISSING_KEY_MESSAGE)
        url = self._url(host, path)
        headers = {"X-Riot-Token": self.api_key or "", "Accept": "application/json"}

        for attempt in range(2):  # 1 essai + 1 nouvel essai max (429)
            try:
                async with self.session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
                    if resp.status == 404:
                        raise NotFoundError(not_found)
                    if resp.status in (401, 403):
                        log.error(
                            "API Riot : accès refusé (HTTP %s) sur %s — clé invalide ou expirée ?",
                            resp.status, path,
                        )
                        raise ExternalServiceError(
                            "La clé API Riot est invalide ou expirée : préviens un admin "
                            "(les clés de développement expirent toutes les 24 h)."
                        )
                    if resp.status == 429:
                        wait = self._retry_after(resp.headers.get("Retry-After"))
                        if attempt == 0 and wait <= self.max_retry_wait:
                            log.warning("API Riot : limite de débit atteinte, nouvel essai dans %.1f s", wait)
                            await asyncio.sleep(wait)
                            continue
                        log.warning("API Riot : limite de débit atteinte (Retry-After=%s)", wait)
                        raise ExternalServiceError(
                            "L'API Riot est très sollicitée en ce moment (limite de requêtes atteinte). "
                            f"Réessaie dans environ {max(1, round(wait))} seconde(s)."
                        )
                    if resp.status >= 500:
                        log.warning("API Riot : erreur serveur HTTP %s sur %s", resp.status, path)
                        raise ExternalServiceError(
                            "Les serveurs de Riot rencontrent un problème. Réessaie dans quelques minutes."
                        )
                    body = (await resp.text())[:200]
                    log.warning("API Riot : réponse inattendue HTTP %s sur %s : %s", resp.status, path, body)
                    raise ExternalServiceError(
                        f"L'API Riot a refusé la requête (HTTP {resp.status}). Vérifie le Riot ID saisi."
                    )
            except (NotFoundError, ExternalServiceError):
                raise
            except asyncio.TimeoutError as exc:
                log.warning("API Riot : délai dépassé sur %s", path)
                raise ExternalServiceError(
                    "L'API Riot ne répond pas (délai dépassé). Réessaie dans quelques minutes."
                ) from exc
            except (aiohttp.ClientError, ValueError) as exc:
                log.warning("API Riot : erreur réseau sur %s : %r", path, exc)
                raise ExternalServiceError(
                    "Impossible de joindre l'API Riot pour le moment. Réessaie dans quelques minutes."
                ) from exc
        # Inatteignable : la boucle retourne ou lève toujours.
        raise ExternalServiceError("L'API Riot est indisponible. Réessaie plus tard.")

    @staticmethod
    def _retry_after(raw: str | None) -> float:
        try:
            return max(0.0, float(raw)) if raw is not None else 1.0
        except ValueError:
            return 1.0
