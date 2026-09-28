"""Affichage des équipes d'un inhouse : embeds (une par partie) + bouton persistant « Voir les équipes ».

- Faille / ARAM : « Partie 1 : 🔵 Équipe 1 vs 🔴 Équipe 2 », joueurs (rôle, Riot ID, rang),
  niveau moyen de chaque équipe, lien MultiGG par équipe, écart de niveau.
- Arena : une embed par partie avec un champ par duo.
- Remplaçants à la fin.

Le bouton ``ih:teams:<event_id>`` affiche les équipes publiées en éphémère.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from bot.core.checks import is_organizer
from bot.core.error_reporting import interaction_guard
from bot.core.errors import NotFoundError, UserFacingError
from bot.features.inhouse.constants import (
    SIDE_NAMES,
    SUBSTITUTES_EMOJI,
    GameMode,
    get_mode,
    team_title,
)
from bot.features.inhouse.players import (
    PlayerCard,
    card_of,
    load_players,
    multigg_url,
    player_line,
    score_label,
)
from bot.repositories.events import Event, EventRepository
from bot.repositories.inhouse import InhouseRepository, InhouseSession
from bot.repositories.inhouse_teams import InhouseTeamRepository, StoredTeams, TeamMember
from bot.repositories.player_roles import role_label
from bot.services.team_builder import LANE_ROLES, PlayerInfo, effective_scores, match_layout
from bot.utils import embeds
from bot.utils.embeds import Colors, truncate
from bot.utils.time import discord_full, discord_ts

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

MAX_EMBEDS = 10
MAX_TOTAL_CHARS = 5800  # limite Discord : 6000 caractères pour toutes les embeds d'un message


@dataclass(slots=True)
class TeamsContext:
    event: Event
    session: InhouseSession
    mode: GameMode
    stored: StoredTeams
    cards: dict[int, PlayerCard]
    scores: dict[int, float]  # rang effectif (inconnu => moyenne des connus)

    def team_score(self, index: int) -> float:
        return sum(self.scores.get(m.discord_id, 0.0) for m in self.stored.team(index))

    def team_average(self, index: int) -> float | None:
        members = self.stored.team(index)
        return self.team_score(index) / len(members) if members else None


async def load_teams_context(bot: "STFBot", event: Event, session: InhouseSession | None = None) -> TeamsContext:
    session = session or await InhouseRepository(bot.db).get(event.id)
    if session is None:
        raise NotFoundError(f"**{event.title}** n'est pas une session d'inhouse.")
    stored = await InhouseTeamRepository(bot.db).get_teams(event.id)
    ids = [*stored.all_player_ids(), *stored.substitutes]
    cards = await load_players(bot, ids)
    infos = [PlayerInfo(uid, (), card_of(cards, uid).rank_score) for uid in ids]
    return TeamsContext(event, session, get_mode(session.game_mode), stored, cards, effective_scores(infos))


# ------------------------------------------------------------------ contrôles
def team_warnings(ctx: TeamsContext, registered_ids: list[int] | None = None) -> list[str]:
    """Incohérences à signaler à l'organisateur (après un ajustement manuel, un désistement…)."""
    warnings: list[str] = []
    mode = ctx.mode
    for index in range(ctx.stored.team_count):
        members = ctx.stored.team(index)
        name = team_title(index, mode.key)
        if len(members) != mode.team_size:
            warnings.append(f"{name} compte **{len(members)}** joueur(s) au lieu de {mode.team_size}.")
        if mode.uses_roles:
            roles = [m.assigned_role for m in members if m.assigned_role]
            doubles = sorted({r for r in roles if roles.count(r) > 1}, key=LANE_ROLES.index)
            if doubles:
                warnings.append(f"{name} : rôle en double → " + ", ".join(role_label(r) for r in doubles))
            missing = [r for r in LANE_ROLES if r not in roles]
            if missing and len(members) >= mode.team_size:
                warnings.append(f"{name} : personne en " + ", ".join(role_label(r) for r in missing))
            no_role = [m for m in members if not m.assigned_role]
            if no_role:
                warnings.append(f"{name} : " + ", ".join(f"<@{m.discord_id}>" for m in no_role) + " sans rôle.")
    if registered_ids is not None:
        in_comp = set(ctx.stored.all_player_ids()) | set(ctx.stored.substitutes)
        gone = [uid for uid in ctx.stored.all_player_ids() if uid not in registered_ids]
        if gone:
            warnings.append("Plus inscrit(s) : " + ", ".join(f"<@{u}>" for u in gone))
        new = [uid for uid in registered_ids if uid not in in_comp]
        if new:
            warnings.append("Inscrit(s) mais absent(s) des équipes : " + ", ".join(f"<@{u}>" for u in new))
    return warnings


# ------------------------------------------------------------------ embeds
def _team_field_value(bot: "STFBot", ctx: TeamsContext, members: list[TeamMember]) -> str:
    lines = [
        player_line(card_of(ctx.cards, m.discord_id), role=m.assigned_role if ctx.mode.uses_roles else None, show_roles=False)
        for m in members
    ] or ["*Équipe vide*"]
    url = multigg_url(bot, ctx.cards, [m.discord_id for m in members])
    if url:
        lines.append(f"[🔗 MultiGG de l'équipe]({url})")
    return truncate("\n".join(lines), 1024)


def _team_field_name(ctx: TeamsContext, index: int) -> str:
    avg = ctx.team_average(index)
    return f"{team_title(index, ctx.mode.key)} · moy. {score_label(avg)}"[:256]


def _gap_text(gap: float) -> str:
    if gap < 50:
        verdict = "🟢 très équilibré"
    elif gap < 120:
        verdict = "🟡 équilibré"
    else:
        verdict = "🟠 déséquilibré"
    return f"⚖️ Écart de niveau moyen : **{gap:.0f} pts** ({verdict} · 100 pts = 1 division)"


def _header_embed(ctx: TeamsContext, *, draft: bool) -> discord.Embed:
    event, mode = ctx.event, ctx.mode
    title = f"{'📝 Aperçu — ' if draft else '⚔️ '}Équipes · {event.title}"
    lines = [
        f"{mode.display} · {mode.format_text}",
        f"📅 {discord_full(event.starts_at)}",
    ]
    n_players = len(ctx.stored.all_player_ids())
    lines.append(f"👥 **{n_players}** joueurs · **{ctx.stored.team_count}** {'duos' if mode.key == 'arena' else 'équipes'}")
    if ctx.session.teams_generated_at:
        lines.append(f"🛠️ Composition du {discord_ts(ctx.session.teams_generated_at, 'f')}")
    embed = discord.Embed(
        title=title[:256],
        description="\n".join(lines),
        color=Colors.WARNING if draft else Colors.INHOUSE,
    )
    if draft:
        embed.set_footer(text="Aperçu visible uniquement par toi · rien n'est encore publié")
    else:
        embed.set_footer(text=f"Inhouse #{event.id} · Bon jeu à tous ! GLHF ⚔️")
    return embed


def _versus_embeds(bot: "STFBot", ctx: TeamsContext) -> list[discord.Embed]:
    layout = match_layout(ctx.mode.key, ctx.stored.team_count)
    result: list[discord.Embed] = []
    for match in sorted(set(layout)):
        teams = [t for t, m in enumerate(layout) if m == match]
        versus = " vs ".join(team_title(t, ctx.mode.key) for t in teams)
        embed = discord.Embed(title=f"Partie {match + 1} : {versus}"[:256], color=Colors.INHOUSE)
        for pos, t in enumerate(teams):
            side = f" · côté {SIDE_NAMES[pos]}" if len(teams) == 2 and pos < 2 else ""
            embed.add_field(
                name=(_team_field_name(ctx, t) + side)[:256],
                value=_team_field_value(bot, ctx, ctx.stored.team(t)),
                inline=False,
            )
        averages = [a for a in (ctx.team_average(t) for t in teams) if a is not None]
        if len(averages) >= 2:
            embed.description = _gap_text(max(averages) - min(averages))
        result.append(embed)
    return result


def _arena_embeds(bot: "STFBot", ctx: TeamsContext) -> list[discord.Embed]:
    layout = match_layout(ctx.mode.key, ctx.stored.team_count)
    result: list[discord.Embed] = []
    for match in sorted(set(layout)):
        teams = [t for t, m in enumerate(layout) if m == match]
        embed = discord.Embed(
            title=f"Partie {match + 1} · {ctx.mode.emoji} Arena ({len(teams)} duos)",
            color=Colors.INHOUSE,
        )
        for t in teams:
            embed.add_field(name=_team_field_name(ctx, t), value=_team_field_value(bot, ctx, ctx.stored.team(t)), inline=True)
        averages = [a for a in (ctx.team_average(t) for t in teams) if a is not None]
        if len(averages) >= 2:
            embed.description = _gap_text(max(averages) - min(averages))
        result.append(embed)
    return result


def _substitutes_embed(ctx: TeamsContext) -> discord.Embed | None:
    subs = ctx.stored.substitutes
    if not subs:
        return None
    lines = [player_line(card_of(ctx.cards, uid), index=i) for i, uid in enumerate(subs, start=1)]
    embed = discord.Embed(
        title=f"{SUBSTITUTES_EMOJI} Remplaçants ({len(subs)})",
        description=truncate("\n".join(lines), 4000)
        + "\n\n*Prêts à entrer en jeu si quelqu'un manque à l'appel !*",
        color=Colors.NEUTRAL,
    )
    return embed


def _fit(embed_list: list[discord.Embed]) -> list[discord.Embed]:
    """Respecte les limites Discord (10 embeds, 6000 caractères au total)."""
    kept: list[discord.Embed] = []
    total = 0
    for e in embed_list:
        size = len(e)
        if len(kept) >= MAX_EMBEDS - 1 or total + size > MAX_TOTAL_CHARS - 200:
            omitted = len(embed_list) - len(kept)
            kept.append(
                embeds.warning(
                    f"{omitted} partie(s) non affichée(s) (limite Discord). "
                    "Utilise le bouton « Voir les équipes » pour tout consulter."
                )
            )
            break
        kept.append(e)
        total += size
    return kept


def build_teams_embeds(bot: "STFBot", ctx: TeamsContext, *, draft: bool = False) -> list[discord.Embed]:
    """Embeds complètes des équipes (en-tête, une par partie, remplaçants)."""
    if not ctx.stored.exists:
        return [
            embeds.info(
                "Les équipes n'ont pas encore été générées.\n"
                "Organisateurs : `/inhouse equipes-generer` 🎲",
                title=f"⚔️ Équipes · {ctx.event.title}",
            )
        ]
    items = [_header_embed(ctx, draft=draft)]
    items += _arena_embeds(bot, ctx) if ctx.mode.key == "arena" else _versus_embeds(bot, ctx)
    if subs := _substitutes_embed(ctx):
        items.append(subs)
    return _fit(items)


def teams_mentions(stored: StoredTeams) -> str:
    return " ".join(f"<@{uid}>" for uid in [*stored.all_player_ids(), *stored.substitutes])


# ------------------------------------------------------------------ bouton persistant
class TeamsButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ih:teams:(?P<id>[0-9]+)"):
    """« Voir les équipes » sous l'annonce d'un inhouse (réponse éphémère)."""

    def __init__(self, event_id: int, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label="Voir les équipes",
                emoji="⚔️",
                style=discord.ButtonStyle.primary,
                custom_id=f"ih:teams:{event_id}",
                row=row,
            )
        )
        self.event_id = event_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ) -> "TeamsButton":
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        async with interaction_guard(interaction):
            bot: STFBot = interaction.client  # type: ignore[assignment]
            event = await EventRepository(bot.db).get(self.event_id)
            if event is None:
                raise NotFoundError("Cet inhouse n'existe plus (il a peut-être été supprimé).")
            session = await InhouseRepository(bot.db).get(event.id)
            if session is None:
                raise NotFoundError("Cet inhouse n'existe plus.")
            if not session.teams_published and not await is_organizer(interaction):
                raise UserFacingError(
                    "Les équipes ne sont pas encore publiées : patience, les organisateurs s'en occupent ! ⏳"
                )
            ctx = await load_teams_context(bot, event, session)
            items = build_teams_embeds(bot, ctx, draft=not session.teams_published)
            await interaction.response.send_message(embeds=items, ephemeral=True)
