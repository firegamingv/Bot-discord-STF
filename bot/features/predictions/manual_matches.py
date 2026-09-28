"""Matchs gérés à la main par les organisateurs (tournois hors LoL Esports, matchs internes…).

- ``/pronos-admin competition-creer nom``                       : crée une compétition manuelle ;
- ``/pronos-admin match-ajouter competition equipe1 equipe2 date [bo] [tournoi] [annoncer]`` ;
- ``/pronos-admin resultat match score``   : saisit le score (ex. ``2-1``) et règle les paris ;
- ``/pronos-admin annuler-match match``    : annule le match et rembourse toutes les mises.

``resultat`` et ``annuler-match`` fonctionnent aussi sur les matchs LoL Esports (correction
manuelle si l'API tarde) et demandent une confirmation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

import discord
from discord import app_commands

from bot.core.checks import organizer_only
from bot.core.error_reporting import BaseView
from bot.core.errors import UserFacingError
from bot.features.predictions.autocomplete import (
    competition_autocomplete,
    resolve_competition,
    resolve_match,
    result_score_autocomplete,
    unsettled_match_autocomplete,
)
from bot.features.predictions.bet_buttons import build_match_card
from bot.features.predictions.embeds import fmt_points
from bot.features.predictions.group import pronos_admin_group
from bot.features.predictions.settlement_service import cancel_match, settle_match
from bot.repositories.competitions import CompetitionRepository
from bot.repositories.matches import STATE_COMPLETED, Match, MatchRepository
from bot.repositories.predictions import STATUS_PENDING, PredictionRepository
from bot.services.betting_rules import possible_scores, winner_from_score
from bot.utils import embeds
from bot.utils.time import DATE_HELP, DateParseError, discord_full, now_utc, parse_user_datetime

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


class ConfirmView(BaseView):
    """Boutons Oui / Non ; exécute ``on_confirm`` si l'auteur confirme."""

    def __init__(self, owner_id: int, on_confirm: Callable[[discord.Interaction], Awaitable[None]]) -> None:
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.on_confirm = on_confirm

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    @discord.ui.button(label="Oui, confirmer", style=discord.ButtonStyle.danger, emoji="✅")
    async def yes(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="⏳ Un instant…", embed=None, view=None)
        await self.on_confirm(interaction)

    @discord.ui.button(label="Non, annuler", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def no(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="Action annulée, rien n'a changé. 👍", embed=None, view=None)


async def _pending_summary(bot: "STFBot", match: Match) -> str:
    pending = await PredictionRepository(bot.db).list_for_match(match.id, status=STATUS_PENDING)
    total = sum(p.stake for p in pending)
    return f"{len(pending)} pari(s) en attente pour {fmt_points(total)} misés."


# ---------------------------------------------------------------------------- compétition
@pronos_admin_group.command(name="competition-creer", description="✍️ Créer une compétition manuelle (matchs saisis à la main)")
@app_commands.describe(nom="Nom de la compétition (ex. Coupe STF, Tournoi interne)")
@organizer_only()
async def create_competition(interaction: discord.Interaction, nom: app_commands.Range[str, 2, 80]) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    comp = await CompetitionRepository(bot.db).create_manual(interaction.guild_id, nom)  # type: ignore[arg-type]
    log.info("Compétition manuelle %s « %s » créée par %s", comp.id, comp.name, interaction.user.id)
    await embeds.reply(interaction, embeds.success(
        f"Compétition **{comp.name}** prête ! Ajoute des matchs avec `/pronos-admin match-ajouter`.",
        title="✍️ Compétition créée",
    ))


# ---------------------------------------------------------------------------- ajout de match
@pronos_admin_group.command(name="match-ajouter", description="➕ Ajouter un match à la main (paris ouverts jusqu'au début)")
@app_commands.describe(
    competition="Compétition (créée avec /pronos-admin competition-creer)",
    equipe1="Nom de l'équipe 1",
    equipe2="Nom de l'équipe 2",
    date="Date et heure de début, ex. « samedi 18h » ou « 12/10 20:30 »",
    bo="Format du match (Bo1 par défaut)",
    tournoi="Nom du tournoi / de l'événement (facultatif, sert aux classements)",
    annoncer="Publier la carte du match dans le salon des pronostics (oui par défaut)",
)
@app_commands.choices(bo=[
    app_commands.Choice(name="Bo1", value=1),
    app_commands.Choice(name="Bo3", value=3),
    app_commands.Choice(name="Bo5", value=5),
])
@app_commands.autocomplete(competition=competition_autocomplete)
@organizer_only()
async def add_match(
    interaction: discord.Interaction,
    competition: str,
    equipe1: app_commands.Range[str, 1, 60],
    equipe2: app_commands.Range[str, 1, 60],
    date: str,
    bo: app_commands.Choice[int] | None = None,
    tournoi: app_commands.Range[str, 1, 80] | None = None,
    annoncer: bool = True,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild_id: int = interaction.guild_id  # type: ignore[assignment]
    comp = await resolve_competition(bot, guild_id, competition)
    assert comp is not None
    try:
        starts_at = parse_user_datetime(date, bot.config.timezone)
    except DateParseError as exc:
        raise UserFacingError(str(exc)) from exc
    if starts_at <= now_utc():
        raise UserFacingError(f"Cette date est déjà passée ({discord_full(starts_at)}). {DATE_HELP}")
    if equipe1.strip().lower() == equipe2.strip().lower():
        raise UserFacingError("Une équipe ne peut pas jouer contre elle-même 😄 Vérifie les noms.")

    match = await MatchRepository(bot.db).create_manual(
        guild_id, comp.id,
        team1_name=equipe1.strip(), team2_name=equipe2.strip(), starts_at=starts_at,
        best_of=bo.value if bo else 1, tournament_name=(tournoi or comp.name).strip(),
    )
    log.info("Match manuel %s (%s) ajouté par %s", match.id, match.title, interaction.user.id)
    card, view = await build_match_card(bot, match)

    posted = ""
    if annoncer:
        settings = await bot.settings.get(guild_id)
        channel = bot.get_channel(settings.predictions_channel_id) if settings.predictions_channel_id else None
        if isinstance(channel, discord.abc.Messageable):
            try:
                await channel.send(content="🆕 **Nouveau match ouvert aux pronostics !**", embed=card, view=view)
                posted = f"\n📣 Annoncé dans <#{settings.predictions_channel_id}>."
            except discord.HTTPException:
                posted = "\n⚠️ Impossible de publier dans le salon des pronostics (vérifie mes permissions)."
        else:
            posted = "\nℹ️ Aucun salon des pronostics configuré (`/config salon-pronos`) : le match n'a pas été annoncé."
    await interaction.response.send_message(
        embeds=[embeds.success(f"Match **#{match.id}** ajouté !{posted}", title="➕ Match créé"), card],
        ephemeral=True,
    )


# ---------------------------------------------------------------------------- résultat
@pronos_admin_group.command(name="resultat", description="🏁 Saisir le résultat d'un match et régler les paris")
@app_commands.describe(match="Le match terminé", score="Score final équipe 1 - équipe 2 (ex. 2-1)")
@app_commands.autocomplete(match=unsettled_match_autocomplete, score=result_score_autocomplete)
@organizer_only()
async def set_result(interaction: discord.Interaction, match: str, score: str) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    found = await resolve_match(bot, interaction.guild_id, match)  # type: ignore[arg-type]
    if found.settled:
        raise UserFacingError("Ce match est déjà réglé : les gains ont été distribués.")
    raw = score.strip().replace(" ", "").replace(":", "-")
    if raw not in possible_scores(found.best_of):
        raise UserFacingError(
            f"Score `{score}` impossible pour un Bo{found.best_of}. "
            f"Scores possibles : {', '.join(possible_scores(found.best_of))}."
        )
    a, b = (int(x) for x in raw.split("-"))
    winner = winner_from_score(a, b)
    summary = await _pending_summary(bot, found)

    async def confirm(inter: discord.Interaction) -> None:
        repo = MatchRepository(bot.db)
        await repo.set_result(found.id, team1_score=a, team2_score=b, winner=winner, state=STATE_COMPLETED)
        report = await settle_match(bot, found.id)
        log.info("Résultat saisi pour le match %s : %s par %s", found.id, raw, inter.user.id)
        if report is None:
            await inter.edit_original_response(content="Ce match avait déjà été réglé entre-temps.")
            return
        await inter.edit_original_response(content=None, embed=embeds.success(
            f"**{found.team_name(winner)}** l'emporte {a}-{b} !\n"
            f"🎉 {len(report.winners)} gagnant(s) • 😢 {report.losers} perdant(s) • "
            f"{fmt_points(report.total_paid)} distribués.",
            title="🏁 Paris réglés",
        ))

    await interaction.response.send_message(
        embed=embeds.warning(
            f"Confirmer le résultat **{found.team_code(1)} {a} - {b} {found.team_code(2)}** "
            f"(victoire de **{found.team_name(winner)}**) ?\n{summary}\nLes gains seront versés immédiatement.",
            title="🏁 Résultat",
        ),
        view=ConfirmView(interaction.user.id, confirm),
        ephemeral=True,
    )


# ---------------------------------------------------------------------------- annulation
@pronos_admin_group.command(name="annuler-match", description="🚫 Annuler un match et rembourser toutes les mises")
@app_commands.describe(match="Le match à annuler")
@app_commands.autocomplete(match=unsettled_match_autocomplete)
@organizer_only()
async def cancel(interaction: discord.Interaction, match: str) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    found = await resolve_match(bot, interaction.guild_id, match)  # type: ignore[arg-type]
    if found.settled:
        raise UserFacingError("Ce match est déjà réglé : impossible de l'annuler après coup.")
    summary = await _pending_summary(bot, found)

    async def confirm(inter: discord.Interaction) -> None:
        report = await cancel_match(bot, found.id)
        log.info("Match %s annulé par %s", found.id, inter.user.id)
        refunded = report.refunded if report else 0
        await inter.edit_original_response(content=None, embed=embeds.success(
            f"Match **{found.title}** annulé : {refunded} pari(s) remboursé(s).", title="🚫 Match annulé",
        ))

    await interaction.response.send_message(
        embed=embeds.warning(
            f"Annuler **{found.title}** ?\n{summary}\nToutes les mises seront remboursées.",
            title="🚫 Annulation",
        ),
        view=ConfirmView(interaction.user.id, confirm),
        ephemeral=True,
    )
