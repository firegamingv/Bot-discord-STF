"""Tâche de fond : cycle de vie automatique des événements.

- à l'heure de début : état « en cours », inscriptions fermées, annonce mise à jour ;
- ``FINISH_AFTER`` (6 h) après le début : état « terminé », boutons désactivés.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from discord.ext import commands, tasks

from bot.features.events.announcement import refresh_event_message
from bot.repositories.events import STATUS_FINISHED, STATUS_ONGOING, STATUS_SCHEDULED, Event, EventRepository
from bot.utils.time import now_utc

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

FINISH_AFTER = timedelta(hours=6)


def lifecycle_transition(event: Event, now: datetime, *, finish_after: timedelta = FINISH_AFTER) -> str | None:
    """Nouvel état de l'événement à cet instant, ou ``None`` s'il n'y a rien à changer (pure)."""
    if not event.is_active or now < event.starts_at:
        return None
    if now >= event.starts_at + finish_after:
        return STATUS_FINISHED
    if event.status == STATUS_SCHEDULED:
        return STATUS_ONGOING
    return None


class EventLifecycleTask(commands.Cog):
    """Fait avancer les événements (programmé → en cours → terminé) chaque minute."""

    def __init__(self, bot: "STFBot") -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.advance_events.start()

    async def cog_unload(self) -> None:
        self.advance_events.cancel()

    @tasks.loop(minutes=1)
    async def advance_events(self) -> None:
        try:
            await self.run_once()
        except Exception:  # noqa: BLE001 - la boucle ne doit jamais mourir
            log.exception("Erreur dans la tâche de cycle de vie des événements")

    @advance_events.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    async def run_once(self, now: datetime | None = None) -> None:
        now = now or now_utc()
        repo = EventRepository(self.bot.db)
        for event in await repo.list_all_active():
            new_status = lifecycle_transition(event, now)
            if new_status is None:
                continue
            try:
                await repo.update(event.id, status=new_status, registration_open=False)
                log.info("Événement %s : %s → %s", event.id, event.status, new_status)
                await refresh_event_message(self.bot, event.id)
            except Exception:  # noqa: BLE001
                log.exception("Impossible de faire avancer l'événement %s", event.id)
