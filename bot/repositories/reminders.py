"""Rappels déjà envoyés avant le début des événements (table ``event_reminders``).

Une ligne (event_id, offset_minutes) = « le rappel X minutes avant a été traité ».
On l'insère **avant** d'envoyer le message pour ne jamais envoyer deux fois le même rappel.
"""

from __future__ import annotations

from datetime import datetime

from bot.db import Database
from bot.utils.time import now_utc, to_db


class ReminderRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def sent_offsets(self, event_id: int) -> set[int]:
        """Délais (en minutes) déjà traités pour cet événement."""
        rows = await self.db.fetchall(
            "SELECT offset_minutes FROM event_reminders WHERE event_id = ?", (event_id,)
        )
        return {r["offset_minutes"] for r in rows}

    async def mark_sent(self, event_id: int, offset_minutes: int, *, sent_at: datetime | None = None) -> bool:
        """Marque le rappel comme traité. Renvoie ``False`` s'il l'était déjà (=> ne pas envoyer)."""
        inserted = await self.db.execute_rowcount(
            "INSERT OR IGNORE INTO event_reminders (event_id, offset_minutes, sent_at) VALUES (?, ?, ?)",
            (event_id, offset_minutes, to_db(sent_at or now_utc())),
        )
        return inserted > 0

    async def reset(self, event_id: int) -> int:
        """Oublie les rappels envoyés (ex. l'événement a été déplacé). Renvoie le nombre supprimé."""
        return await self.db.execute_rowcount(
            "DELETE FROM event_reminders WHERE event_id = ?", (event_id,)
        )
