"""Liaison d'un membre Discord à son compte Riot (logique métier, sans interface).

API publique (voir ARCHITECTURE.md) : ``link_riot_account(bot, user, game_name, tag_line)``.
Également utilisé par les modales / boutons des autres domaines (ex. annonces d'inhouse).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import TYPE_CHECKING

import discord

from bot.core.errors import ExternalServiceError, UserFacingError
from bot.repositories.riot_accounts import RiotAccount, RiotAccountRepository

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

RIOT_ID_HELP = (
    "Le Riot ID s'écrit `Pseudo#TAG` (ex. `Faker#KR1`) : tu le trouves en haut à droite du "
    "client League of Legends, en survolant ton pseudo."
)
# Règles Riot : pseudo de 3 à 16 caractères, tag de 3 à 5 caractères alphanumériques.
_TAG_RE = re.compile(r"^[^\W_]{3,5}$")


def parse_riot_id(game_name: str, tag_line: str | None = None) -> tuple[str, str]:
    """Normalise un Riot ID saisi par un humain.

    Accepte ``("Pseudo", "EUW")``, ``("Pseudo#EUW", None)`` ou ``("Pseudo#EUW", "")``.
    Lève ``UserFacingError`` avec un message explicite si le format est invalide.
    """
    name = " ".join((game_name or "").split())  # espaces multiples -> un seul
    tag = (tag_line or "").strip().lstrip("#").strip()
    if "#" in name:
        name, _, tag_from_name = name.rpartition("#")
        name = name.strip()
        tag = tag_from_name.strip() or tag
    if not name or not tag:
        raise UserFacingError(f"Il manque une partie de ton Riot ID. {RIOT_ID_HELP}", title="Riot ID incomplet")
    if not 3 <= len(name) <= 16:
        raise UserFacingError(
            f"Le pseudo **{discord.utils.escape_markdown(name)}** doit faire entre 3 et 16 caractères. "
            f"{RIOT_ID_HELP}",
            title="Riot ID invalide",
        )
    if not _TAG_RE.match(tag):
        raise UserFacingError(
            f"Le tag **#{discord.utils.escape_markdown(tag)}** doit contenir 3 à 5 lettres ou chiffres. "
            f"{RIOT_ID_HELP}",
            title="Riot ID invalide",
        )
    return name, tag


async def link_riot_account(
    bot: "STFBot", user: discord.abc.User, game_name: str, tag_line: str
) -> RiotAccount:
    """Lie le compte Riot ``game_name#tag_line`` au membre ``user`` et renvoie la liaison.

    - Si l'API Riot est configurée : vérifie l'existence du compte (PUUID), refuse s'il est
      déjà lié à un autre membre, puis récupère le rang Solo/Duo (best effort).
    - Sinon : mode dégradé, la liaison est enregistrée sans PUUID (``account.verified`` = False).
    """
    name, tag = parse_riot_id(game_name, tag_line)
    repo = RiotAccountRepository(bot.db)
    riot = bot.riot

    if not riot.enabled:
        account = await repo.link(
            user.id, puuid=None, game_name=name, tag_line=tag,
            platform=riot.platform, display_name=user.display_name,
        )
        log.info("Compte Riot %s lié à %s (%s) SANS vérification (API désactivée)", account.riot_id, user, user.id)
        return account

    dto = await riot.get_account_by_riot_id(name, tag)
    owner = await repo.get_by_puuid(dto.puuid)
    if owner is not None and owner.discord_id != user.id:
        raise UserFacingError(
            f"Le compte **{discord.utils.escape_markdown(dto.riot_id)}** est déjà lié à <@{owner.discord_id}>. "
            "Si c'est bien le tien, demande à cette personne de faire `/compte delier` "
            "ou contacte un organisateur.",
            title="Compte déjà utilisé",
        )

    try:
        account = await repo.link(
            user.id, puuid=dto.puuid, game_name=dto.game_name, tag_line=dto.tag_line,
            platform=riot.platform, display_name=user.display_name,
        )
    except sqlite3.IntegrityError as exc:  # course : lié entre-temps par quelqu'un d'autre
        raise UserFacingError(
            "Ce compte Riot vient d'être lié à un autre membre. Contacte un organisateur si c'est une erreur.",
            title="Compte déjà utilisé",
        ) from exc

    try:
        rank = await riot.get_solo_rank(dto.puuid, riot.platform, use_cache=False)
    except ExternalServiceError as exc:
        # Le compte est lié ; le rang sera récupéré plus tard (/compte actualiser ou tâche auto).
        log.warning("Rang indisponible pour %s après liaison : %s", dto.riot_id, exc.message)
    else:
        await repo.update_rank(
            user.id,
            tier=rank.tier if rank else None,
            division=rank.division if rank else None,
            league_points=rank.league_points if rank else None,
        )
    log.info("Compte Riot %s (vérifié) lié à %s (%s)", dto.riot_id, user, user.id)
    return await repo.get(user.id)  # type: ignore[return-value]
