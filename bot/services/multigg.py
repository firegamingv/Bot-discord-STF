"""Liens « MultiGG » : recherche multiple op.gg de plusieurs Riot ID d'un coup.

Format : https://www.op.gg/multisearch/{region}?summoners=Nom1%23TAG1,Nom2%23TAG2
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import quote

OPGG_BASE = "https://www.op.gg/multisearch"
DEFAULT_REGION = "euw"
#: op.gg n'affiche confortablement qu'une dizaine de joueurs par recherche multiple.
MAX_PER_LINK = 10

_PLATFORM_TO_REGION: dict[str, str] = {
    "euw1": "euw",
    "eun1": "eune",
    "na1": "na",
    "kr": "kr",
    "br1": "br",
    "jp1": "jp",
    "la1": "lan",
    "la2": "las",
    "oc1": "oce",
    "tr1": "tr",
    "ru": "ru",
}


def opgg_region(platform: str | None) -> str:
    """Plateforme Riot (``euw1``…) -> région op.gg (``euw``…). EUW par défaut."""
    return _PLATFORM_TO_REGION.get((platform or "").strip().lower(), DEFAULT_REGION)


def _clean(riot_ids: Iterable[str]) -> list[str]:
    result: list[str] = []
    for rid in riot_ids:
        rid = (rid or "").strip()
        if rid and rid not in result:
            result.append(rid)
    return result


def multisearch_url(riot_ids: list[str], platform: str | None = None) -> str | None:
    """Lien op.gg multisearch pour ces Riot ID (``Nom#TAG``). ``None`` si la liste est vide."""
    ids = _clean(riot_ids)
    if not ids:
        return None
    summoners = ",".join(quote(rid, safe="") for rid in ids)
    return f"{OPGG_BASE}/{opgg_region(platform)}?summoners={summoners}"


def multisearch_urls(
    riot_ids: list[str], platform: str | None = None, *, per_link: int = MAX_PER_LINK
) -> list[str]:
    """Comme ``multisearch_url`` mais découpé en plusieurs liens de ``per_link`` joueurs."""
    ids = _clean(riot_ids)
    urls: list[str] = []
    for i in range(0, len(ids), max(per_link, 1)):
        url = multisearch_url(ids[i : i + per_link], platform)
        if url:
            urls.append(url)
    return urls
