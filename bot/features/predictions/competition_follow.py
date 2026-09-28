"""``/pronos-admin competition-suivre`` et ``competition-retirer`` : suivre une ligue en la
cherchant par son nom (plus pratique que le menu paginé de ``/pronos-admin competitions``).

L'autocomplétion interroge la liste complète des ligues LoL Esports (mise en cache 30 min)
et cherche dans le nom, le slug et la région : taper « lfl », « france » ou « masters » suffit.
"""

from __future__ import annotations

import logging
import time
import unicodedata
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.checks import organizer_only
from bot.core.errors import NotFoundError, UserFacingError
from bot.features.predictions.autocomplete import competition_autocomplete, resolve_competition
from bot.features.predictions.group import pronos_admin_group
from bot.features.predictions.sync_service import sync_competitions
from bot.repositories.competitions import SOURCE_LOLESPORTS, CompetitionRepository
from bot.services.lolesports_api import LeagueDTO
from bot.utils import embeds

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

LEAGUES_TTL = 30 * 60
_cache: tuple[float, list[LeagueDTO]] | None = None


async def all_leagues(bot: "STFBot") -> list[LeagueDTO]:
    """Toutes les ligues LoL Esports (cache mémoire de 30 minutes)."""
    global _cache
    if _cache is not None and time.monotonic() - _cache[0] < LEAGUES_TTL:
        return _cache[1]
    leagues = await bot.esports.get_leagues()
    _cache = (time.monotonic(), leagues)
    return leagues


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return "".join(ch for ch in text.lower() if ch.isalnum())


def search_leagues(leagues: list[LeagueDTO], query: str, limit: int = 25) -> list[LeagueDTO]:
    """Ligues dont le nom, le slug ou la région contient la recherche (les plus pertinentes d'abord)."""
    q = _norm(query)
    if not q:
        return sorted(leagues, key=lambda lg: (lg.priority, lg.name))[:limit]
    scored = []
    for lg in leagues:
        name, slug, region = _norm(lg.name), _norm(lg.slug), _norm(lg.region)
        if q in (name, slug):
            rank = 0
        elif name.startswith(q) or slug.startswith(q):
            rank = 1
        elif q in name or q in slug:
            rank = 2
        elif q in region:
            rank = 3
        else:
            continue
        scored.append((rank, lg.priority, lg.name, lg))
    scored.sort(key=lambda t: t[:3])
    return [t[3] for t in scored[:limit]]


async def league_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    try:
        leagues = await all_leagues(bot)
    except Exception:  # noqa: BLE001 - une autocomplétion ne doit jamais lever
        log.warning("Liste des ligues LoL Esports indisponible pour l'autocomplétion", exc_info=True)
        return []
    return [
        app_commands.Choice(name=f"{lg.name} · {lg.region}"[:100], value=lg.id)
        for lg in search_leagues(leagues, current)
    ]


@pronos_admin_group.command(name="competition-suivre", description="Suivre une compétition LoL Esports en la cherchant par son nom")
@app_commands.describe(ligue="Tape le nom de la ligue (ex. LFL, EMEA Masters, LEC…)")
@app_commands.autocomplete(ligue=league_autocomplete)
@organizer_only()
async def follow_competition(interaction: discord.Interaction, ligue: str) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    await interaction.response.defer(ephemeral=True, thinking=True)
    leagues = await all_leagues(bot)
    league = next((lg for lg in leagues if lg.id == ligue), None)
    if league is None:
        matches = search_leagues(leagues, ligue, limit=1)
        if not matches:
            raise NotFoundError(
                f"Aucune compétition « {ligue} » sur LoL Esports. Choisis une proposition de la liste ; "
                "si ta compétition n'y est pas, crée-la avec `/pronos-admin competition-creer` "
                "et ajoute ses matchs avec `/pronos-admin match-ajouter`."
            )
        league = matches[0]

    repo = CompetitionRepository(bot.db)
    existing = await repo.get_by_external(interaction.guild_id, SOURCE_LOLESPORTS, league.id)
    if existing is not None and existing.followed:
        raise UserFacingError(f"**{league.name}** est déjà suivie.")
    await repo.upsert(
        interaction.guild_id, source=SOURCE_LOLESPORTS, external_id=league.id, name=league.name,
        slug=league.slug, image_url=league.image, followed=True,
    )
    competition = await repo.get_by_external(interaction.guild_id, SOURCE_LOLESPORTS, league.id)
    log.info("Compétition %s suivie sur le serveur %s par %s", league.name, interaction.guild_id, interaction.user.id)

    report = await sync_competitions(bot, [competition])
    text = f"**{league.name}** est maintenant suivie. "
    if report.errors:
        text += "La récupération des matchs a échoué pour l'instant, je réessaierai automatiquement dans 10 minutes."
    else:
        text += (
            f"{report.created} match(s) récupéré(s) pour les 7 prochains jours. "
            "Les paris sont ouverts dans `/pronos matchs`."
        )
    embed = embeds.success(text)
    if league.image:
        embed.set_thumbnail(url=league.image)
    await interaction.followup.send(embed=embed, ephemeral=True)


@pronos_admin_group.command(name="competition-retirer", description="Ne plus suivre une compétition (paris et historique conservés)")
@app_commands.describe(competition="La compétition à ne plus suivre")
@app_commands.autocomplete(competition=competition_autocomplete)
@organizer_only()
async def unfollow_competition(interaction: discord.Interaction, competition: str) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    comp = await resolve_competition(bot, interaction.guild_id, competition)
    if comp is None or not comp.followed:
        raise NotFoundError("Compétition introuvable parmi celles suivies. Choisis-la dans la liste proposée.")
    await CompetitionRepository(bot.db).set_followed(comp.id, False)
    log.info("Compétition %s retirée sur le serveur %s par %s", comp.name, interaction.guild_id, interaction.user.id)
    await embeds.reply(
        interaction,
        embeds.success(f"**{comp.name}** n'est plus suivie. Les paris et le classement déjà enregistrés sont conservés."),
    )
