"""Ajustements manuels des équipes générées.

- ``/inhouse equipes-echanger session joueur1 joueur2`` : les deux joueurs échangent leur place
  (équipe ET rôle). Fonctionne aussi avec un remplaçant.
- ``/inhouse equipes-deplacer session joueur equipe [role]`` : place un joueur dans une équipe
  (0 = remplaçants). Sans rôle précisé en Faille, on lui donne le meilleur rôle libre.

Chaque ajustement affiche l'aperçu mis à jour, les incohérences éventuelles (rôle en double,
équipe incomplète…) et un bouton « 📢 Publier / Republier ».
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands

from bot.core.checks import organizer_only
from bot.core.errors import UserFacingError
from bot.features.inhouse.autocomplete import inhouse_autocomplete, resolve_inhouse, team_autocomplete
from bot.features.inhouse.constants import ROLE_CHOICES, SUBSTITUTES_EMOJI, get_mode, team_title
from bot.features.inhouse.group import inhouse_group
from bot.features.inhouse.teams_display import build_teams_embeds, load_teams_context, team_warnings
from bot.features.inhouse.teams_publish import PublishTeamsView
from bot.repositories.events import Event
from bot.repositories.inhouse import InhouseSession
from bot.repositories.inhouse_teams import SUBSTITUTES_INDEX, InhouseTeamRepository, StoredTeams, TeamMember
from bot.repositories.participants import REGISTERED, ParticipantRepository
from bot.repositories.player_roles import PlayerRoleRepository, role_label
from bot.services.team_builder import LANE_ROLES
from bot.utils import embeds
from bot.utils.embeds import Colors

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


def _place(member: TeamMember | None, mode_key: str) -> str:
    if member is None:
        return "hors des équipes"
    if member.is_substitute:
        return f"{SUBSTITUTES_EMOJI} Remplaçants"
    text = team_title(member.team_index, mode_key)
    if member.assigned_role:
        text += f" ({role_label(member.assigned_role)})"
    return text


async def _require_teams(bot: "STFBot", event: Event) -> StoredTeams:
    stored = await InhouseTeamRepository(bot.db).get_teams(event.id)
    if not stored.exists:
        raise UserFacingError(
            f"Aucune équipe n'a encore été générée pour **{event.title}**. "
            "Lance d'abord `/inhouse equipes-generer` 🎲"
        )
    return stored


async def _send_preview(
    interaction: discord.Interaction, event: Event, session: InhouseSession, change: str
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    ctx = await load_teams_context(bot, event, session)
    registered = [p.discord_id for p in await ParticipantRepository(bot.db).list(event.id, status=REGISTERED)]
    warnings = team_warnings(ctx, registered)
    summary = discord.Embed(title="🔧 Équipes ajustées", description=change, color=Colors.SUCCESS)
    if warnings:
        summary.color = Colors.WARNING
        summary.add_field(name="⚠️ À vérifier", value="\n".join(f"• {w}" for w in warnings)[:1024], inline=False)
    if session.teams_published:
        summary.add_field(
            name="📢 Affichage public",
            value="Le message public n'est pas encore à jour : clique sur **Republier les équipes**.",
            inline=False,
        )
    items = [summary, *build_teams_embeds(bot, ctx, draft=True, reserved=[summary])]
    view = PublishTeamsView(event.id, interaction.user.id, republish=session.teams_published)
    await interaction.followup.send(embeds=items, view=view, ephemeral=True)


@inhouse_group.command(name="equipes-echanger", description="Échanger deux joueurs (équipe et rôle)")
@app_commands.describe(
    session="La session d'inhouse (tape pour chercher)",
    joueur1="Premier joueur",
    joueur2="Second joueur (un remplaçant fonctionne aussi)",
)
@app_commands.autocomplete(session=inhouse_autocomplete)
@organizer_only()
async def swap_command(
    interaction: discord.Interaction, session: int, joueur1: discord.User, joueur2: discord.User
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event, ih = await resolve_inhouse(interaction, session)
    if joueur1.id == joueur2.id:
        raise UserFacingError("Choisis deux joueurs différents 😉")
    stored = await _require_teams(bot, event)
    mode = get_mode(ih.game_mode)
    a, b = stored.find(joueur1.id), stored.find(joueur2.id)
    for user, member in ((joueur1, a), (joueur2, b)):
        if member is None:
            raise UserFacingError(
                f"{user.mention} ne fait pas partie de la composition. "
                "Pour ajouter un inscrit, utilise `/inhouse equipes-deplacer`."
            )
    await interaction.response.defer(ephemeral=True, thinking=True)
    await InhouseTeamRepository(bot.db).swap_players(event.id, joueur1.id, joueur2.id)
    log.info("Inhouse %s : %s et %s échangés par %s", event.id, joueur1.id, joueur2.id, interaction.user.id)
    change = (
        f"🔁 {joueur1.mention} : {_place(a, mode.key)} → **{_place(b, mode.key)}**\n"
        f"🔁 {joueur2.mention} : {_place(b, mode.key)} → **{_place(a, mode.key)}**"
    )
    await _send_preview(interaction, event, ih, change)


def _best_free_role(prefs: list[str], taken: set[str]) -> str | None:
    for role in prefs:
        if role in LANE_ROLES and role not in taken:
            return role
    for role in LANE_ROLES:
        if role not in taken:
            return role
    return None


@inhouse_group.command(name="equipes-deplacer", description="Placer un joueur dans une équipe (ou en remplaçant)")
@app_commands.describe(
    session="La session d'inhouse (tape pour chercher)",
    joueur="Le joueur à déplacer (il doit être inscrit)",
    equipe="Équipe de destination (0 = remplaçants)",
    role="Rôle à lui attribuer (Faille) — par défaut : son meilleur rôle encore libre",
)
@app_commands.choices(role=ROLE_CHOICES)
@app_commands.autocomplete(session=inhouse_autocomplete, equipe=team_autocomplete)
@organizer_only()
async def move_command(
    interaction: discord.Interaction,
    session: int,
    joueur: discord.User,
    equipe: app_commands.Range[int, 0, 64],
    role: Optional[app_commands.Choice[str]] = None,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event, ih = await resolve_inhouse(interaction, session)
    mode = get_mode(ih.game_mode)
    stored = await _require_teams(bot, event)
    current = stored.find(joueur.id)
    if current is None:
        participant = await ParticipantRepository(bot.db).get(event.id, joueur.id)
        if participant is None or participant.status != REGISTERED:
            raise UserFacingError(
                f"{joueur.mention} n'est pas inscrit·e à **{event.title}** : "
                "il ou elle doit d'abord cliquer sur « Rejoindre »."
            )
    if equipe > stored.team_count:
        raise UserFacingError(
            f"L'équipe {equipe} n'existe pas : il y a {stored.team_count} équipes "
            "(choisis-la dans la liste, 0 = remplaçants)."
        )
    target = SUBSTITUTES_INDEX if equipe == 0 else equipe - 1

    new_role: str | None = None
    if target != SUBSTITUTES_INDEX and mode.uses_roles:
        taken = {m.assigned_role for m in stored.team(target) if m.discord_id != joueur.id and m.assigned_role}
        if role is not None:
            new_role = role.value
        elif current is not None and current.assigned_role and current.assigned_role not in taken:
            new_role = current.assigned_role
        else:
            prefs = await PlayerRoleRepository(bot.db).get(joueur.id)
            new_role = _best_free_role(prefs, taken)
    if current is not None and current.team_index == target and current.assigned_role == new_role:
        await embeds.reply(interaction, embeds.info(f"{joueur.mention} est déjà à cette place 🙂"))
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    await InhouseTeamRepository(bot.db).move_player(event.id, joueur.id, target, new_role)
    log.info("Inhouse %s : %s déplacé vers l'équipe %s (%s) par %s", event.id, joueur.id, target, new_role, interaction.user.id)
    new_place = _place(TeamMember(event.id, target, joueur.id, new_role), mode.key)
    change = f"➡️ {joueur.mention} : {_place(current, mode.key)} → **{new_place}**"
    await _send_preview(interaction, event, ih, change)
