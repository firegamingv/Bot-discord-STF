"""``/pronos-admin competitions`` : choisir les compétitions LoL Esports suivies par le serveur.

Affiche la liste des ligues (majeures d'abord : Worlds, MSI, LEC, LCK, LPL, LTA, LFL…) dans un
menu à choix multiples, 25 par page. Cocher = suivre ; décocher = ne plus suivre (les matchs
et paris déjà enregistrés sont conservés). Le bouton « Terminé » lance une synchronisation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from bot.core.checks import organizer_only
from bot.core.error_reporting import BaseView
from bot.features.predictions.group import pronos_admin_group
from bot.features.predictions.sync_service import sync_guild
from bot.repositories.competitions import SOURCE_LOLESPORTS, CompetitionRepository
from bot.services.lolesports_api import LeagueDTO
from bot.utils import embeds
from bot.utils.embeds import Colors

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

PAGE_SIZE = 25

# Ordre d'affichage prioritaire (slugs LoL Esports ; les autres suivent l'ordre de l'API).
MAJOR_SLUGS = [
    "worlds", "msi", "first_stand", "lec", "lck", "lpl", "lta_north", "lta_south", "lta_cross",
    "lcs", "cblol-brazil", "lcp", "lfl", "emea_masters", "lck_challengers_league", "pcs", "vcs",
    "ljl-japan", "la_ligue_francaise", "nlc", "superliga", "prime_league", "lla", "tcl",
    "hitpoint_masters", "ultraliga", "elite_series",
]
MAJOR_KEYWORDS = ["worlds", "msi", "first stand", "lec", "lck", "lpl", "lta", "lcs", "lfl", "emea masters"]


def sort_leagues(leagues: list[LeagueDTO]) -> list[LeagueDTO]:
    def key(league: LeagueDTO) -> tuple[int, int, str]:
        slug = league.slug.lower()
        if slug in MAJOR_SLUGS:
            return (0, MAJOR_SLUGS.index(slug), league.name)
        name = league.name.lower()
        for i, kw in enumerate(MAJOR_KEYWORDS):
            if kw in name:
                return (1, i, league.name)
        return (2, league.priority, league.name.lower())

    return sorted(leagues, key=key)


class LeagueSelect(discord.ui.Select):
    def __init__(self, leagues: list[LeagueDTO], followed_ids: set[str], page: int, pages: int) -> None:
        options = [
            discord.SelectOption(
                label=league.name[:100],
                value=league.id,
                description=(league.region or league.slug)[:100],
                default=league.id in followed_ids,
                emoji="🏆",
            )
            for league in leagues
        ]
        super().__init__(
            placeholder=f"Coche les compétitions à suivre (page {page + 1}/{pages})",
            options=options,
            min_values=0,
            max_values=len(options),
        )
        self.leagues = leagues

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CompetitionPickerView = self.view  # type: ignore[assignment]
        await view.save_page(interaction, self.leagues, set(self.values))


class CompetitionPickerView(BaseView):
    def __init__(self, bot: "STFBot", owner_id: int, guild_id: int, leagues: list[LeagueDTO], followed: set[str]) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.leagues = leagues
        self.followed = followed
        self.page = 0
        self.pages = max(1, (len(leagues) + PAGE_SIZE - 1) // PAGE_SIZE)
        self._build()

    def _page_leagues(self) -> list[LeagueDTO]:
        return self.leagues[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]

    def _build(self) -> None:
        self.clear_items()
        self.add_item(LeagueSelect(self._page_leagues(), self.followed, self.page, self.pages))
        if self.pages > 1:
            prev_btn = discord.ui.Button(label="Page précédente", emoji="◀️", disabled=self.page == 0, row=1)
            next_btn = discord.ui.Button(label="Page suivante", emoji="▶️", disabled=self.page >= self.pages - 1, row=1)
            prev_btn.callback = self._prev  # type: ignore[method-assign]
            next_btn.callback = self._next  # type: ignore[method-assign]
            self.add_item(prev_btn)
            self.add_item(next_btn)
        done = discord.ui.Button(label="Terminé : synchroniser les matchs", emoji="✅",
                                 style=discord.ButtonStyle.success, row=2)
        done.callback = self._done  # type: ignore[method-assign]
        self.add_item(done)

    def embed(self) -> discord.Embed:
        names = [lg.name for lg in self.leagues if lg.id in self.followed]
        embed = discord.Embed(
            title="🏆 Compétitions suivies",
            description=(
                "Coche dans le menu les compétitions sur lesquelles les membres pourront parier. "
                "Chaque changement est **enregistré immédiatement**.\n"
                "Les compétitions majeures sont en premier ; utilise les flèches pour voir les autres."
            ),
            color=Colors.ESPORT,
        )
        embed.add_field(
            name=f"✅ Suivies ({len(names)})",
            value=embeds.truncate(", ".join(names) if names else "Aucune pour l'instant."),
            inline=False,
        )
        embed.set_footer(text=f"{len(self.leagues)} compétitions disponibles • page {self.page + 1}/{self.pages}")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await embeds.reply(interaction, embeds.error("Ce menu appartient à quelqu'un d'autre : lance ta propre commande."))
            return False
        return True

    async def save_page(self, interaction: discord.Interaction, leagues: list[LeagueDTO], selected: set[str]) -> None:
        repo = CompetitionRepository(self.bot.db)
        added, removed = [], []
        for league in leagues:
            existing = await repo.get_by_external(self.guild_id, SOURCE_LOLESPORTS, league.id)
            if league.id in selected:
                if existing is None or not existing.followed:
                    added.append(league.name)
                await repo.upsert(
                    self.guild_id, source=SOURCE_LOLESPORTS, external_id=league.id, name=league.name,
                    slug=league.slug, image_url=league.image, followed=True,
                )
                self.followed.add(league.id)
            else:
                if existing is not None and existing.followed:
                    await repo.set_followed(existing.id, False)
                    removed.append(league.name)
                self.followed.discard(league.id)
        if added or removed:
            log.info("Compétitions du serveur %s : +%s -%s (par %s)", self.guild_id, added, removed, interaction.user.id)
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _prev(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page = min(self.pages - 1, self.page + 1)
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _done(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            embed=self.embed().set_footer(text="⏳ Synchronisation des matchs en cours…"), view=None
        )
        if not self.followed:
            await interaction.edit_original_response(
                embed=self.embed().set_footer(text="Aucune compétition suivie : rien à synchroniser.")
            )
            return
        report = await sync_guild(self.bot, self.guild_id)
        text = (f"🔄 Synchro : {report.created} nouveau(x) match(s), {report.updated} mis à jour"
                + (f" • ⚠️ {'; '.join(report.errors)}" if report.errors else ""))
        await interaction.edit_original_response(embed=self.embed().set_footer(text=text[:2048]))


@pronos_admin_group.command(name="competitions", description="🏆 Choisir les compétitions LoL Esports à suivre")
@organizer_only()
async def pick_competitions(interaction: discord.Interaction) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild_id: int = interaction.guild_id  # type: ignore[assignment]
    await interaction.response.defer(ephemeral=True, thinking=True)
    leagues = sort_leagues(await bot.esports.get_leagues())
    if not leagues:
        await embeds.reply(interaction, embeds.warning(
            "L'API LoL Esports n'a renvoyé aucune compétition. Réessaie dans quelques minutes."
        ))
        return
    existing = await CompetitionRepository(bot.db).list(guild_id, followed_only=True, source=SOURCE_LOLESPORTS)
    followed = {c.external_id for c in existing}
    view = CompetitionPickerView(bot, interaction.user.id, guild_id, leagues, followed)
    await interaction.followup.send(embed=view.embed(), view=view, ephemeral=True)
