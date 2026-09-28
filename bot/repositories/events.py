"""Événements génériques (table ``events``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import aiosqlite

from bot.db import Database
from bot.utils.time import from_db, now_utc, to_db

STATUS_SCHEDULED = "scheduled"
STATUS_ONGOING = "ongoing"
STATUS_FINISHED = "finished"
STATUS_CANCELLED = "cancelled"
ACTIVE_STATUSES = (STATUS_SCHEDULED, STATUS_ONGOING)

_EDITABLE = {
    "title", "description", "starts_at", "max_participants", "registration_open",
    "status", "channel_id", "message_id",
}


@dataclass(slots=True)
class Event:
    id: int
    guild_id: int
    type: str
    title: str
    description: str | None
    starts_at: datetime
    max_participants: int | None
    registration_open: bool
    status: str
    channel_id: int | None
    message_id: int | None
    created_by: int
    created_at: datetime

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def has_started(self) -> bool:
        return self.starts_at <= now_utc()

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "Event":
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            type=row["type"],
            title=row["title"],
            description=row["description"],
            starts_at=from_db(row["starts_at"]),
            max_participants=row["max_participants"],
            registration_open=bool(row["registration_open"]),
            status=row["status"],
            channel_id=row["channel_id"],
            message_id=row["message_id"],
            created_by=row["created_by"],
            created_at=from_db(row["created_at"]),
        )


class EventRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        *,
        guild_id: int,
        type: str,
        title: str,
        description: str | None,
        starts_at: datetime,
        max_participants: int | None,
        created_by: int,
        registration_open: bool = True,
    ) -> Event:
        event_id = await self.db.execute(
            """INSERT INTO events (guild_id, type, title, description, starts_at,
                                   max_participants, registration_open, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, type, title, description, to_db(starts_at), max_participants,
             int(registration_open), created_by),
        )
        return await self.get(event_id)  # type: ignore[return-value]

    async def get(self, event_id: int) -> Event | None:
        row = await self.db.fetchone("SELECT * FROM events WHERE id = ?", (event_id,))
        return Event.from_row(row) if row else None

    async def get_in_guild(self, guild_id: int, event_id: int) -> Event | None:
        row = await self.db.fetchone(
            "SELECT * FROM events WHERE id = ? AND guild_id = ?", (event_id, guild_id)
        )
        return Event.from_row(row) if row else None

    async def update(self, event_id: int, **values) -> Event | None:
        unknown = set(values) - _EDITABLE
        if unknown:
            raise ValueError(f"Champs non modifiables : {unknown}")
        if not values:
            return await self.get(event_id)
        if "starts_at" in values and isinstance(values["starts_at"], datetime):
            values["starts_at"] = to_db(values["starts_at"])
        if "registration_open" in values:
            values["registration_open"] = int(bool(values["registration_open"]))
        assignments = ", ".join(f"{k} = :{k}" for k in values)
        await self.db.execute(
            f"UPDATE events SET {assignments}, updated_at = :now WHERE id = :id",
            {**values, "now": to_db(now_utc()), "id": event_id},
        )
        return await self.get(event_id)

    async def delete(self, event_id: int) -> None:
        await self.db.execute("DELETE FROM events WHERE id = ?", (event_id,))

    async def list_upcoming(
        self, guild_id: int, *, type: str | None = None, limit: int = 25
    ) -> list[Event]:
        """Événements actifs (programmés ou en cours), du plus proche au plus lointain."""
        sql = "SELECT * FROM events WHERE guild_id = ? AND status IN (?, ?)"
        params: list = [guild_id, *ACTIVE_STATUSES]
        if type is not None:
            sql += " AND type = ?"
            params.append(type)
        sql += " ORDER BY starts_at ASC LIMIT ?"
        params.append(limit)
        return [Event.from_row(r) for r in await self.db.fetchall(sql, params)]

    async def search(
        self, guild_id: int, text: str, *, type: str | None = None, active_only: bool = True, limit: int = 25
    ) -> list[Event]:
        """Pour l'autocomplétion des commandes."""
        sql = "SELECT * FROM events WHERE guild_id = ? AND (title LIKE ? OR CAST(id AS TEXT) = ?)"
        params: list = [guild_id, f"%{text}%", text.strip().lstrip("#")]
        if active_only:
            sql += " AND status IN (?, ?)"
            params.extend(ACTIVE_STATUSES)
        if type is not None:
            sql += " AND type = ?"
            params.append(type)
        sql += " ORDER BY starts_at ASC LIMIT ?"
        params.append(limit)
        return [Event.from_row(r) for r in await self.db.fetchall(sql, params)]

    async def list_all_active(self) -> list[Event]:
        """Tous serveurs confondus (pour les tâches de fond : rappels, clôture…)."""
        rows = await self.db.fetchall(
            "SELECT * FROM events WHERE status IN (?, ?) ORDER BY starts_at ASC", ACTIVE_STATUSES
        )
        return [Event.from_row(r) for r in rows]
