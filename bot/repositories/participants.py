"""Inscriptions aux événements (table ``event_participants``), avec liste d'attente."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bot.db import Database
from bot.utils.time import from_db

REGISTERED = "registered"
WAITLIST = "waitlist"


@dataclass(slots=True)
class Participant:
    event_id: int
    discord_id: int
    status: str
    joined_at: datetime

    @property
    def on_waitlist(self) -> bool:
        return self.status == WAITLIST


class ParticipantRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def list(self, event_id: int, *, status: str | None = None) -> list[Participant]:
        """Participants dans l'ordre d'inscription."""
        sql = "SELECT * FROM event_participants WHERE event_id = ?"
        params: list = [event_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY joined_at ASC, rowid ASC"
        rows = await self.db.fetchall(sql, params)
        return [
            Participant(r["event_id"], r["discord_id"], r["status"], from_db(r["joined_at"]))
            for r in rows
        ]

    async def get(self, event_id: int, discord_id: int) -> Participant | None:
        r = await self.db.fetchone(
            "SELECT * FROM event_participants WHERE event_id = ? AND discord_id = ?",
            (event_id, discord_id),
        )
        return Participant(r["event_id"], r["discord_id"], r["status"], from_db(r["joined_at"])) if r else None

    async def count(self, event_id: int, *, status: str = REGISTERED) -> int:
        return await self.db.fetchval(
            "SELECT COUNT(*) FROM event_participants WHERE event_id = ? AND status = ?",
            (event_id, status),
        )

    async def add(self, event_id: int, discord_id: int, *, max_participants: int | None) -> Participant:
        """Inscrit le membre, en liste d'attente si l'événement est complet.

        Renvoie la participation (existante si déjà inscrit).
        """
        existing = await self.get(event_id, discord_id)
        if existing is not None:
            return existing
        status = REGISTERED
        if max_participants is not None and await self.count(event_id) >= max_participants:
            status = WAITLIST
        await self.db.execute(
            "INSERT INTO event_participants (event_id, discord_id, status) VALUES (?, ?, ?)",
            (event_id, discord_id, status),
        )
        return await self.get(event_id, discord_id)  # type: ignore[return-value]

    async def remove(self, event_id: int, discord_id: int) -> bool:
        return bool(
            await self.db.execute_rowcount(
                "DELETE FROM event_participants WHERE event_id = ? AND discord_id = ?",
                (event_id, discord_id),
            )
        )

    async def promote_waitlist(self, event_id: int, max_participants: int | None) -> list[int]:
        """Fait monter les premiers de la liste d'attente s'il reste de la place.

        Renvoie les IDs Discord promus (pour les prévenir).
        """
        waiting = await self.list(event_id, status=WAITLIST)
        if not waiting:
            return []
        free = len(waiting) if max_participants is None else max_participants - await self.count(event_id)
        promoted = [p.discord_id for p in waiting[: max(free, 0)]]
        if promoted:
            await self.db.executemany(
                "UPDATE event_participants SET status = ? WHERE event_id = ? AND discord_id = ?",
                [(REGISTERED, event_id, uid) for uid in promoted],
            )
        return promoted

    async def rebalance(self, event_id: int, max_participants: int | None) -> tuple[list[int], list[int]]:
        """Après modification de la capacité : renvoie (promus, rétrogradés en attente)."""
        if max_participants is None:
            return await self.promote_waitlist(event_id, None), []
        registered = await self.list(event_id, status=REGISTERED)
        demoted = [p.discord_id for p in registered[max_participants:]]
        if demoted:
            await self.db.executemany(
                "UPDATE event_participants SET status = ? WHERE event_id = ? AND discord_id = ?",
                [(WAITLIST, event_id, uid) for uid in demoted],
            )
        return await self.promote_waitlist(event_id, max_participants), demoted
