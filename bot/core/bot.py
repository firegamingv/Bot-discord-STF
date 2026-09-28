"""Classe principale du bot : initialise les services partagés et charge les fonctionnalités."""

from __future__ import annotations

import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Config
from bot.core.error_reporting import report_error
from bot.db import Database
from bot.repositories.settings import SettingsRepository
from bot.services.lolesports_api import LolEsportsClient
from bot.services.riot_api import RiotClient

log = logging.getLogger(__name__)

# Chaque paquet de ``bot/features`` est une extension discord.py (fonction ``setup``).
# Pour ajouter une fonctionnalité : créer un paquet et l'ajouter ici.
FEATURES: tuple[str, ...] = (
    "bot.features.admin",
    "bot.features.help",
    "bot.features.account",
    "bot.features.events",
    "bot.features.inhouse",
    "bot.features.predictions",
)


class STFTree(app_commands.CommandTree):
    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        await report_error(interaction, error)


class STFBot(commands.Bot):
    """Bot STF. Services accessibles depuis n'importe quelle interaction via ``interaction.client`` :

    - ``config``   : configuration (.env)
    - ``db``       : base SQLite (``bot.db.Database``)
    - ``settings`` : paramètres par serveur (``SettingsRepository``)
    - ``http``     : ne pas confondre ! la session aiohttp partagée est ``web``
    - ``web``      : ``aiohttp.ClientSession`` partagée
    - ``riot``     : client API Riot (``RiotClient``)
    - ``esports``  : client API LoL Esports (``LolEsportsClient``)
    """

    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.members = False  # inutile : on travaille uniquement avec les interactions
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            tree_cls=STFTree,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=True, users=True),
        )
        self.config = config
        self.db = Database(config.database_path)
        self.settings = SettingsRepository(self.db)
        self.web: aiohttp.ClientSession | None = None
        self.riot: RiotClient
        self.esports: LolEsportsClient

    async def setup_hook(self) -> None:
        await self.db.connect()
        self.web = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        self.riot = RiotClient(
            self.web,
            api_key=self.config.riot_api_key,
            region=self.config.riot_region,
            platform=self.config.riot_platform,
        )
        self.esports = LolEsportsClient(self.web, api_key=self.config.lolesports_api_key)

        for feature in FEATURES:
            try:
                await self.load_extension(feature)
                log.info("Fonctionnalité chargée : %s", feature)
            except Exception:  # noqa: BLE001 - une fonctionnalité cassée ne doit pas tuer le bot
                log.exception("Impossible de charger %s", feature)

        await self._sync_commands()

    async def _sync_commands(self) -> None:
        try:
            if self.config.guild_id:
                guild = discord.Object(id=self.config.guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info("%d commandes synchronisées sur le serveur %s", len(synced), self.config.guild_id)
            else:
                synced = await self.tree.sync()
                log.info("%d commandes synchronisées globalement (propagation jusqu'à 1h)", len(synced))
        except discord.HTTPException:
            log.exception("Échec de la synchronisation des commandes slash")

    async def on_ready(self) -> None:
        log.info("Connecté en tant que %s (%s) sur %d serveur(s)", self.user, self.user.id, len(self.guilds))
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="/aide • inhouses & pronos")
        )

    async def close(self) -> None:
        log.info("Arrêt du bot…")
        try:
            await super().close()
        finally:
            if self.web is not None:
                await self.web.close()
            await self.db.close()
