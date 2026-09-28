"""Liens op.gg (petit helper local au domaine « compte »)."""

from __future__ import annotations

from urllib.parse import quote

# Plateforme Riot -> région utilisée dans les URL op.gg
OPGG_REGIONS: dict[str, str] = {
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


def opgg_profile_url(game_name: str, tag_line: str, platform: str | None) -> str:
    """URL op.gg du joueur, ex. ``https://www.op.gg/summoners/euw/Pseudo-EUW``."""
    region = OPGG_REGIONS.get((platform or "euw1").lower(), "euw")
    slug = quote(f"{game_name}-{tag_line}", safe="")
    return f"https://www.op.gg/summoners/{region}/{slug}"
