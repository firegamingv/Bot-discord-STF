"""Mise en forme partagée par les commandes ``/compte`` (rang, rôles, statut de vérification)."""

from __future__ import annotations

from bot.repositories.player_roles import role_label
from bot.repositories.riot_accounts import RiotAccount
from bot.utils.time import discord_ts

TIER_EMOJIS: dict[str, str] = {
    "IRON": "⚙️",
    "BRONZE": "🥉",
    "SILVER": "🥈",
    "GOLD": "🥇",
    "PLATINUM": "💠",
    "EMERALD": "💚",
    "DIAMOND": "💎",
    "MASTER": "🟣",
    "GRANDMASTER": "🔴",
    "CHALLENGER": "👑",
}


def rank_line(account: RiotAccount, *, with_date: bool = False) -> str:
    """Ex. « 🥇 Or II (54 LP) », « Non classé » ou « Inconnu » (compte non vérifié)."""
    if not account.verified and not account.rank_tier:
        return "❔ Inconnu *(compte non vérifié)*"
    if account.rank_updated_at is None and not account.rank_tier:
        return "❔ Pas encore récupéré — essaie `/compte actualiser`"
    if not account.rank_tier:
        text = "🌱 Non classé en Solo/Duo"
    else:
        text = f"{TIER_EMOJIS.get(account.rank_tier, '🏅')} {account.rank_label}"
    if with_date and account.rank_updated_at is not None:
        text += f"\n-# Mis à jour {discord_ts(account.rank_updated_at, 'R')}"
    return text


def verification_line(account: RiotAccount) -> str:
    if account.verified:
        return "✅ Vérifié auprès de Riot"
    return "⚠️ Non vérifié (API Riot indisponible lors de la liaison)"


def roles_line(roles: list[str], *, empty: str = "*Aucun rôle choisi*") -> str:
    """Ex. « **1.** 🛡️ Top · **2.** 🌲 Jungle »."""
    if not roles:
        return empty
    return " · ".join(f"**{i}.** {role_label(r)}" for i, r in enumerate(roles, start=1))
