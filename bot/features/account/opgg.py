"""Liens op.gg (petit helper local au domaine « compte »)."""

from __future__ import annotations

from urllib.parse import quote

from bot.services.multigg import opgg_region


def opgg_profile_url(game_name: str, tag_line: str, platform: str | None) -> str:
    """URL op.gg du joueur, ex. ``https://www.op.gg/summoners/euw/Pseudo-EUW``."""
    region = opgg_region(platform or "euw1")
    slug = quote(f"{game_name}-{tag_line}", safe="")
    return f"https://www.op.gg/summoners/{region}/{slug}"
