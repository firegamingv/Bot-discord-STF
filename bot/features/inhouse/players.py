"""Fiches joueurs des inhouses : compte Riot + rôles, mis en forme pour les embeds.

Centralise le chargement (une requête par table pour tous les joueurs) et l'affichage
(Riot ID, rang, rôles en emojis, liens MultiGG) utilisés par l'annonce, la liste des
inscrits et l'affichage des équipes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bot.features.account.display import TIER_EMOJIS
from bot.repositories.player_roles import LOL_ROLE_EMOJIS, PlayerRoleRepository
from bot.repositories.riot_accounts import TIER_LABELS, TIER_ORDER, RiotAccount, RiotAccountRepository
from bot.services.multigg import multisearch_url, multisearch_urls
from bot.services.team_builder import FILL, LANE_ROLES, PlayerInfo

if TYPE_CHECKING:
    from bot.core.bot import STFBot

_DIVISIONS = ("IV", "III", "II", "I")


@dataclass(slots=True)
class PlayerCard:
    discord_id: int
    account: RiotAccount | None = None
    roles: list[str] = field(default_factory=list)

    @property
    def linked(self) -> bool:
        return self.account is not None

    @property
    def riot_id(self) -> str | None:
        return self.account.riot_id if self.account else None

    @property
    def rank_score(self) -> float | None:
        return self.account.rank_score if self.account else None

    @property
    def main_role(self) -> str | None:
        return self.roles[0] if self.roles else None

    def rank_text(self) -> str:
        """« 🥇 Or II » (court, pour les listes)."""
        acc = self.account
        if acc is None:
            return "—"
        if not acc.rank_tier:
            return "🌱 Non classé" if acc.rank_updated_at else "❔ Rang inconnu"
        emoji = TIER_EMOJIS.get(acc.rank_tier, "🏅")
        label = TIER_LABELS.get(acc.rank_tier, acc.rank_tier.title())
        if acc.rank_tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
            return f"{emoji} {label} {acc.league_points or 0} LP"
        return f"{emoji} {label} {acc.rank_division or ''}".rstrip()

    def roles_emojis(self) -> str:
        """« 🛡️🌲 » ou « — » si aucun rôle."""
        return "".join(LOL_ROLE_EMOJIS.get(r, "") for r in self.roles) or "—"


async def load_players(bot: "STFBot", discord_ids: Iterable[int]) -> dict[int, PlayerCard]:
    ids = list(dict.fromkeys(discord_ids))
    accounts = await RiotAccountRepository(bot.db).get_many(ids)
    roles = await PlayerRoleRepository(bot.db).get_many(ids)
    return {uid: PlayerCard(uid, accounts.get(uid), roles.get(uid, [])) for uid in ids}


def card_of(cards: dict[int, PlayerCard], discord_id: int) -> PlayerCard:
    return cards.get(discord_id) or PlayerCard(discord_id)


# ------------------------------------------------------------------ affichage
def player_line(
    card: PlayerCard,
    *,
    index: int | None = None,
    role: str | None = None,
    show_roles: bool = True,
) -> str:
    """Ex. « `1.` <@id> · **Faker#KR1** · 🥇 Or II · 🔮🛡️ »."""
    parts: list[str] = []
    prefix = f"`{index:>2}.` " if index is not None else ""
    if role is not None:
        prefix += f"{LOL_ROLE_EMOJIS.get(role, '❔')} "
    parts.append(f"{prefix}<@{card.discord_id}>")
    if card.linked:
        parts.append(f"**{card.riot_id}**")
        parts.append(card.rank_text())
    else:
        parts.append("⚠️ *compte LoL non lié*")
    if show_roles:
        parts.append(card.roles_emojis())
    return " · ".join(parts)


def score_label(score: float | None) -> str:
    """Rang approximatif correspondant à un score (moyenne d'équipe) : « 🥇 Or II »."""
    if score is None:
        return "❔"
    if score >= 2800:
        return f"{TIER_EMOJIS['MASTER']} Maître+ ({score - 2800:.0f} LP)"
    score = max(score, 0.0)
    tier = TIER_ORDER[min(int(score // 400), TIER_ORDER.index("DIAMOND"))]
    division = _DIVISIONS[min(int((score % 400) // 100), 3)]
    return f"{TIER_EMOJIS.get(tier, '🏅')} {TIER_LABELS[tier]} {division}"


def main_role_counts(cards: Iterable[PlayerCard]) -> Counter[str]:
    """Nombre de joueurs par rôle principal (``fill`` compris, ``none`` = aucun rôle)."""
    counts: Counter[str] = Counter()
    for card in cards:
        main = card.main_role
        counts[main if main in (*LANE_ROLES, FILL) else "none"] += 1
    return counts


def role_summary(counts: Counter[str]) -> str:
    """« 🛡️ 2 · 🌲 1 · 🔮 3 · 🏹 0 · 💖 1 · 🎲 1 »."""
    parts = [f"{LOL_ROLE_EMOJIS[r]} {counts.get(r, 0)}" for r in (*LANE_ROLES, FILL)]
    if counts.get("none"):
        parts.append(f"❔ {counts['none']}")
    return " · ".join(parts)


def missing_roles_text(counts: Counter[str], needed_per_role: int) -> str | None:
    """« Il manque : 🏹 ×2 · 💖 ×1 (les 🎲 fill peuvent compléter) » ou None si tout est couvert."""
    missing = [(r, needed_per_role - counts.get(r, 0)) for r in LANE_ROLES if counts.get(r, 0) < needed_per_role]
    if not missing:
        return None
    text = "Il manque : " + " · ".join(f"{LOL_ROLE_EMOJIS[r]} ×{n}" for r, n in missing)
    flexible = counts.get(FILL, 0) + counts.get("none", 0)
    if flexible:
        text += f" *(dont une partie couverte par {flexible} 🎲 fill)*"
    return text


# ------------------------------------------------------------------ équipes / MultiGG
def to_player_infos(ordered_ids: Iterable[int], cards: dict[int, PlayerCard]) -> list[PlayerInfo]:
    """Entrée de ``team_builder`` dans l'ordre d'inscription."""
    infos = []
    for uid in ordered_ids:
        card = card_of(cards, uid)
        infos.append(PlayerInfo(discord_id=uid, roles=tuple(card.roles), rank_score=card.rank_score))
    return infos


def platform_for(bot: "STFBot", cards: Iterable[PlayerCard]) -> str:
    """Plateforme majoritaire des comptes (sinon celle du bot) pour les liens op.gg."""
    platforms = Counter(c.account.platform for c in cards if c.account and c.account.platform)
    if platforms:
        return platforms.most_common(1)[0][0]
    return bot.config.riot_platform


def multigg_url(bot: "STFBot", cards: dict[int, PlayerCard], ids: Iterable[int]) -> str | None:
    selected = [card_of(cards, uid) for uid in ids]
    riot_ids = [c.riot_id for c in selected if c.riot_id]
    return multisearch_url(riot_ids, platform_for(bot, selected))


def multigg_links(bot: "STFBot", cards: dict[int, PlayerCard], ids: Iterable[int], *, label: str = "MultiGG") -> str | None:
    """Liens Markdown (par paquets de 10 joueurs) : « [🔗 MultiGG 1](…) · [🔗 MultiGG 2](…) »."""
    selected = [card_of(cards, uid) for uid in ids]
    riot_ids = [c.riot_id for c in selected if c.riot_id]
    urls = multisearch_urls(riot_ids, platform_for(bot, selected))
    if not urls:
        return None
    if len(urls) == 1:
        return f"[🔗 {label}]({urls[0]})"
    return " · ".join(f"[🔗 {label} {i}]({url})" for i, url in enumerate(urls, start=1))
