"""``/inhouse equipes-generer`` : constituer des équipes équilibrées, avec aperçu avant publication.

Déroulé :
1. rafraîchit les rangs des inscrits (API Riot, si configurée) ;
2. lance ``team_builder`` (rôles + équilibrage des niveaux) et **enregistre** la composition ;
3. affiche un aperçu éphémère avec trois boutons :
   « 📢 Publier » · « 🔄 Regénérer » (nouvelle graine = autre composition quasi optimale) ·
   « ✖ Annuler » (restaure la composition précédente, ou aucune).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.checks import ensure_organizer, organizer_only
from bot.core.error_reporting import BaseView
from bot.core.errors import UserFacingError
from bot.features.account.rank_refresh import refresh_ranks
from bot.features.events.announcement import link_button
from bot.features.inhouse.autocomplete import inhouse_autocomplete, resolve_inhouse
from bot.features.inhouse.constants import GameMode, get_mode
from bot.features.inhouse.group import inhouse_group
from bot.features.inhouse.players import PlayerCard, load_players, to_player_infos
from bot.features.inhouse.teams_display import build_teams_embeds, load_teams_context
from bot.features.inhouse.teams_publish import publish_teams, published_embed
from bot.repositories.events import Event, EventRepository
from bot.repositories.inhouse import InhouseRepository, InhouseSession
from bot.repositories.inhouse_teams import InhouseTeamRepository, StoredTeams
from bot.repositories.participants import REGISTERED, ParticipantRepository
from bot.services.team_builder import NotEnoughPlayersError, TeamsResult, build_teams, role_stats
from bot.utils import embeds
from bot.utils.embeds import Colors

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ logique
async def generate_and_save(
    bot: "STFBot", event: Event, session: InhouseSession, *, seed: int
) -> tuple[TeamsResult, dict[int, PlayerCard]]:
    """Construit les équipes à partir des inscrits (ordre d'inscription) et les enregistre."""
    mode = get_mode(session.game_mode)
    registered = [p.discord_id for p in await ParticipantRepository(bot.db).list(event.id, status=REGISTERED)]
    if len(registered) < mode.min_players:
        raise UserFacingError(
            f"Il faut au moins **{mode.min_players}** inscrits pour une partie en {mode.display} "
            f"(actuellement : **{len(registered)}**). Relance la commande quand il y aura du monde !",
            title="Pas assez de joueurs",
        )
    cards = await load_players(bot, registered)
    try:
        # Calcul pur et potentiellement long (grands inhouses) : hors de la boucle d'événements.
        result = await asyncio.to_thread(
            build_teams, to_player_infos(registered, cards), mode.key, team_size=mode.team_size, seed=seed
        )
    except NotEnoughPlayersError as exc:
        raise UserFacingError(
            f"Pas assez de joueurs pour former les équipes : il en faut au moins {exc.required}."
        ) from exc
    await InhouseTeamRepository(bot.db).save_teams(
        event.id, [team.players for team in result.teams], result.substitutes
    )
    log.info(
        "Équipes générées pour l'inhouse %s (%s, %d équipes, %d remplaçants, graine %s, écart %.0f)",
        event.id, mode.key, len(result.teams), len(result.substitutes), seed, result.average_gap,
    )
    return result, cards


def _quality_embed(mode: GameMode, result: TeamsResult, cards: dict[int, PlayerCard], seed: int) -> discord.Embed:
    lines = [f"⚖️ Écart de niveau moyen : **{result.average_gap:.0f} pts** *(100 pts = 1 division)*"]
    if mode.uses_roles:
        infos = to_player_infos([pid for t in result.teams for pid in t.member_ids], cards)
        stats = role_stats(result, infos)
        total = sum(stats.values()) or 1
        lines.append(
            f"🧩 Rôles : **{stats['main']}/{total}** au rôle principal · {stats['secondary']} secondaire"
            f" · {stats['other'] + stats['fill']} en fill · {stats['offrole']} hors de leurs choix"
        )
    unlinked = [pid for t in result.teams for pid in t.member_ids if not cards.get(pid) or not cards[pid].linked]
    unranked = [
        pid for t in result.teams for pid in t.member_ids
        if cards.get(pid) and cards[pid].linked and cards[pid].rank_score is None
    ]
    if unranked or unlinked:
        lines.append(f"❔ {len(unranked) + len(unlinked)} joueur(s) sans rang connu : niveau moyen utilisé.")
    if result.substitutes:
        lines.append(f"🪑 {len(result.substitutes)} remplaçant(s) : les derniers inscrits.")
    embed = discord.Embed(title="🎲 Équipes générées", description="\n".join(lines), color=Colors.INFO)
    embed.set_footer(text=f"Graine {seed} · « Regénérer » propose une autre composition de qualité équivalente")
    return embed


async def build_preview(
    bot: "STFBot", event: Event, session: InhouseSession, result: TeamsResult, cards: dict[int, PlayerCard], seed: int
) -> list[discord.Embed]:
    mode = get_mode(session.game_mode)
    session = await InhouseRepository(bot.db).get(event.id) or session
    ctx = await load_teams_context(bot, event, session)
    quality = _quality_embed(mode, result, cards, seed)
    if session.teams_published:
        quality.add_field(
            name="📢 Déjà publiées",
            value="Les équipes affichées publiquement ne changeront qu'en cliquant sur **Publier**.",
            inline=False,
        )
    return [quality, *build_teams_embeds(bot, ctx, draft=True, reserved=[quality])]


# ------------------------------------------------------------------ aperçu interactif
class GeneratePreviewView(BaseView):
    def __init__(self, event_id: int, author_id: int, previous: StoredTeams) -> None:
        super().__init__(timeout=900)
        self.event_id = event_id
        self.author_id = author_id
        self.previous = previous
        self.message: discord.InteractionMessage | discord.WebhookMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=embeds.error("Seule la personne qui a lancé la génération peut utiliser ces boutons."),
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass

    async def _load(self, bot: "STFBot") -> tuple[Event, InhouseSession]:
        event = await EventRepository(bot.db).get(self.event_id)
        session = await InhouseRepository(bot.db).get(self.event_id)
        if event is None or session is None:
            raise UserFacingError("Cet inhouse n'existe plus.")
        return event, session

    @discord.ui.button(label="Publier", emoji="📢", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await ensure_organizer(interaction)
        bot: STFBot = interaction.client  # type: ignore[assignment]
        await interaction.response.defer()
        event, session = await self._load(bot)
        message, created = await publish_teams(bot, event, session)
        self.stop()
        view = discord.ui.View()
        view.add_item(link_button(message.jump_url, "Voir les équipes"))
        tips = embeds.info(
            "Besoin d'un ajustement ? `/inhouse equipes-echanger` ou `/inhouse equipes-deplacer`, "
            "puis `/inhouse equipes-publier` pour mettre le message à jour.",
            title="💡 Et ensuite ?",
        )
        await interaction.edit_original_response(embeds=[published_embed(message, created), tips], view=view)

    @discord.ui.button(label="Regénérer", emoji="🔄", style=discord.ButtonStyle.primary)
    async def regenerate(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await ensure_organizer(interaction)
        bot: STFBot = interaction.client  # type: ignore[assignment]
        await interaction.response.defer()
        event, session = await self._load(bot)
        seed = random.randrange(1, 1_000_000)
        result, cards = await generate_and_save(bot, event, session, seed=seed)
        items = await build_preview(bot, event, session, result, cards, seed)
        await interaction.edit_original_response(embeds=items, view=self)

    @discord.ui.button(label="Annuler", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bot: STFBot = interaction.client  # type: ignore[assignment]
        repo = InhouseTeamRepository(bot.db)
        if self.previous.exists:
            await repo.save_teams(self.event_id, self.previous.as_lists(), self.previous.substitutes)
            text = "Génération annulée : la composition précédente a été restaurée."
        else:
            await repo.clear(self.event_id)
            text = "Génération annulée : aucune équipe n'est enregistrée."
        log.info("Génération d'équipes annulée pour l'inhouse %s", self.event_id)
        self.stop()
        await interaction.response.edit_message(embeds=[embeds.info(f"✖️ {text}")], view=None)


# ------------------------------------------------------------------ commande
@inhouse_group.command(name="equipes-generer", description="Générer des équipes équilibrées (aperçu avant publication)")
@app_commands.describe(session="La session d'inhouse (tape pour chercher)")
@app_commands.autocomplete(session=inhouse_autocomplete)
@organizer_only()
async def generate_teams_command(interaction: discord.Interaction, session: int) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event, ih = await resolve_inhouse(interaction, session)
    if not event.is_active:
        raise UserFacingError(f"**{event.title}** est terminé : impossible de regénérer les équipes.")
    await interaction.response.defer(ephemeral=True, thinking=True)

    registered = [p.discord_id for p in await ParticipantRepository(bot.db).list(event.id, status=REGISTERED)]
    await refresh_ranks(bot, registered)  # ne lève jamais ; au pire on garde les rangs connus

    previous = await InhouseTeamRepository(bot.db).get_teams(event.id)
    seed = random.randrange(1, 1_000_000)
    result, cards = await generate_and_save(bot, event, ih, seed=seed)
    items = await build_preview(bot, event, ih, result, cards, seed)
    view = GeneratePreviewView(event.id, interaction.user.id, previous)
    view.message = await interaction.followup.send(embeds=items, view=view, ephemeral=True, wait=True)
