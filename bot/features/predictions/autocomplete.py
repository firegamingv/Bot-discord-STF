"""Autocomplétions des commandes de pronostics + résolution des valeurs choisies.

Les valeurs renvoyées à Discord sont des IDs (``"42"``) ; les fonctions ``resolve_*``
les retransforment en objets et expliquent quoi faire si la saisie est invalide
(un membre peut toujours taper du texte libre au lieu de choisir une suggestion).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.errors import NotFoundError
from bot.features.predictions.embeds import short_local
from bot.repositories.competitions import Competition, CompetitionRepository
from bot.repositories.matches import Match, MatchRepository
from bot.services.betting_rules import exact_score_allowed, possible_scores

if TYPE_CHECKING:
    from bot.core.bot import STFBot

MAX_CHOICES = 25


def _trim(label: str) -> str:
    return label if len(label) <= 100 else label[:99] + "…"


def _match_label(bot: "STFBot", match: Match, competitions: dict[int, Competition]) -> str:
    comp = competitions.get(match.competition_id)
    prefix = f"{comp.name} · " if comp else ""
    return _trim(
        f"{prefix}{match.short_title} · Bo{match.best_of} · {short_local(match.starts_at, bot.config.timezone)}"
    )


async def _competition_map(bot: "STFBot", guild_id: int) -> dict[int, Competition]:
    comps = await CompetitionRepository(bot.db).list(guild_id, followed_only=False)
    return {c.id: c for c in comps}


# ---------------------------------------------------------------------------- compétitions
async def competition_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if interaction.guild_id is None:
        return []
    bot: STFBot = interaction.client  # type: ignore[assignment]
    comps = await CompetitionRepository(bot.db).search(interaction.guild_id, current.strip(), limit=MAX_CHOICES)
    return [
        app_commands.Choice(name=_trim(f"{'✍️' if c.is_manual else '🏆'} {c.name}"), value=str(c.id))
        for c in comps
    ]


async def resolve_competition(bot: "STFBot", guild_id: int, raw: str | None) -> Competition | None:
    if raw is None or not raw.strip():
        return None
    repo = CompetitionRepository(bot.db)
    value = raw.strip()
    if value.isdigit():
        comp = await repo.get_in_guild(guild_id, int(value))
        if comp:
            return comp
    found = await repo.search(guild_id, value, followed_only=False, limit=1)
    if found:
        return found[0]
    raise NotFoundError(
        f"Je ne trouve pas la compétition « {value} ». Choisis-la dans la liste proposée "
        "(les organisateurs ajoutent des compétitions avec `/pronos-admin competitions`)."
    )


# ---------------------------------------------------------------------------- matchs
async def bettable_match_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Matchs à venir, paris encore ouverts."""
    if interaction.guild_id is None:
        return []
    bot: STFBot = interaction.client  # type: ignore[assignment]
    matches = await MatchRepository(bot.db).search(
        interaction.guild_id, current, bettable_only=True, limit=MAX_CHOICES
    )
    comps = await _competition_map(bot, interaction.guild_id)
    return [app_commands.Choice(name=_match_label(bot, m, comps), value=str(m.id)) for m in matches]


async def unsettled_match_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Pour les organisateurs : matchs non réglés (à venir, en cours, terminés sans règlement)."""
    if interaction.guild_id is None:
        return []
    bot: STFBot = interaction.client  # type: ignore[assignment]
    matches = await MatchRepository(bot.db).search(
        interaction.guild_id, current, unsettled_only=True, limit=MAX_CHOICES
    )
    comps = await _competition_map(bot, interaction.guild_id)
    return [app_commands.Choice(name=_match_label(bot, m, comps), value=str(m.id)) for m in matches]


async def resolve_match(bot: "STFBot", guild_id: int, raw: str) -> Match:
    value = (raw or "").strip().lstrip("#")
    repo = MatchRepository(bot.db)
    if value.isdigit():
        match = await repo.get_in_guild(guild_id, int(value))
        if match:
            return match
    found = await repo.search(guild_id, value, limit=1) if value else []
    if found:
        return found[0]
    raise NotFoundError(
        f"Je ne trouve pas le match « {raw} ». Commence à taper le nom d'une équipe et "
        "choisis le match dans la liste, ou consulte `/pronos matchs`."
    )


# ---------------------------------------------------------------------------- équipes / scores
async def _match_from_namespace(interaction: discord.Interaction) -> Match | None:
    raw = getattr(interaction.namespace, "match", None)
    if not raw or interaction.guild_id is None:
        return None
    bot: STFBot = interaction.client  # type: ignore[assignment]
    try:
        return await resolve_match(bot, interaction.guild_id, str(raw))
    except NotFoundError:
        return None


async def team_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    match = await _match_from_namespace(interaction)
    if match is None:
        return [app_commands.Choice(name="⬅️ Choisis d'abord le match", value="0")]
    choices = [
        app_commands.Choice(name=_trim(f"🔵 {match.team1_name} ({match.team_code(1)})"), value="1"),
        app_commands.Choice(name=_trim(f"🔴 {match.team2_name} ({match.team_code(2)})"), value="2"),
    ]
    text = current.lower().strip()
    return [c for c in choices if not text or text in c.name.lower()] or choices


def resolve_team(match: Match, raw: str) -> str:
    """``"1"``/``"2"``, ou un nom/code d'équipe tapé à la main."""
    value = (raw or "").strip().lower()
    if value in ("1", "2"):
        return value
    for team in ("1", "2"):
        if value and value in (match.team_name(team).lower(), match.team_code(team).lower()):
            return team
    raise NotFoundError(
        f"Équipe « {raw} » inconnue pour {match.title}. Choisis "
        f"**{match.team1_name}** ou **{match.team2_name}** dans la liste."
    )


async def score_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    match = await _match_from_namespace(interaction)
    if match is None:
        return [app_commands.Choice(name="⬅️ Choisis d'abord le match", value="0-0")]
    if not exact_score_allowed(match.best_of):
        return [app_commands.Choice(name=f"Pas de score exact en Bo{match.best_of}", value="0-0")]
    out = []
    for score in possible_scores(match.best_of):
        a, b = score.split("-")
        winner = match.team_code(1) if int(a) > int(b) else match.team_code(2)
        out.append(app_commands.Choice(
            name=_trim(f"{match.team_code(1)} {a} - {b} {match.team_code(2)} (victoire {winner})"), value=score
        ))
    return out


async def result_score_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Scores finaux possibles pour saisir un résultat (tous formats, dont Bo1)."""
    match = await _match_from_namespace(interaction)
    if match is None:
        return []
    out = []
    for score in possible_scores(match.best_of):
        a, b = score.split("-")
        out.append(app_commands.Choice(
            name=_trim(f"{match.team_code(1)} {a} - {b} {match.team_code(2)}"), value=score
        ))
    return out


# ---------------------------------------------------------------------------- tournois
async def tournament_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if interaction.guild_id is None:
        return []
    bot: STFBot = interaction.client  # type: ignore[assignment]
    competition_id = None
    raw_comp = getattr(interaction.namespace, "competition", None)
    if raw_comp and str(raw_comp).isdigit():
        competition_id = int(raw_comp)
    names = await MatchRepository(bot.db).list_tournaments(
        interaction.guild_id, competition_id=competition_id, text=current.strip(), limit=MAX_CHOICES
    )
    return [app_commands.Choice(name=_trim(f"🎪 {n}"), value=n[:100]) for n in names]
