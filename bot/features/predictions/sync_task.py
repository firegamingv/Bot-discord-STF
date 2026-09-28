"""Tâche de fond : synchronise les matchs LoL Esports toutes les 10 minutes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from discord.ext import commands, tasks

from bot.features.predictions.sync_service import sync_all
from bot.repositories.matches import MatchRepository

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


class LolEsportsSyncTask(commands.Cog):
    def __init__(self, bot: "STFBot") -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.sync_loop.start()

    async def cog_unload(self) -> None:
        self.sync_loop.cancel()

    @tasks.loop(minutes=10)
    async def sync_loop(self) -> None:
        try:
            # Matchs saisis à la main : l'heure de début passée, on les marque « en cours ».
            await MatchRepository(self.bot.db).mark_live_started()
            report = await sync_all(self.bot)
            for error in report.errors:
                log.warning("Synchro LoL Esports : %s", error)
        except Exception:  # noqa: BLE001 - la boucle ne doit jamais mourir
            log.exception("Erreur pendant la synchronisation LoL Esports")

    @sync_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()
