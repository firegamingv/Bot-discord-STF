"""Paramètres par serveur (salons, rôle organisateur, rappels, barème des pronostics)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from bot.db import Database

_EDITABLE = {
    "announce_channel_id",
    "predictions_channel_id",
    "organizer_role_id",
    "reminder_offsets",
    "daily_points",
    "starting_points",
    "odds_winner",
    "odds_exact_score",
}


@dataclass(slots=True)
class GuildSettings:
    guild_id: int
    announce_channel_id: int | None = None
    predictions_channel_id: int | None = None
    organizer_role_id: int | None = None
    reminder_offsets: list[int] = field(default_factory=lambda: [1440, 60, 15])
    daily_points: int = 100
    starting_points: int = 500
    odds_winner: float = 2.0
    odds_exact_score: float = 3.5


class SettingsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache: dict[int, GuildSettings] = {}

    async def get(self, guild_id: int) -> GuildSettings:
        if guild_id in self._cache:
            return self._cache[guild_id]
        row = await self.db.fetchone("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        if row is None:
            await self.db.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
            settings = GuildSettings(guild_id=guild_id)
        else:
            settings = GuildSettings(
                guild_id=row["guild_id"],
                announce_channel_id=row["announce_channel_id"],
                predictions_channel_id=row["predictions_channel_id"],
                organizer_role_id=row["organizer_role_id"],
                reminder_offsets=sorted(json.loads(row["reminder_offsets"] or "[]"), reverse=True),
                daily_points=row["daily_points"],
                starting_points=row["starting_points"],
                odds_winner=row["odds_winner"],
                odds_exact_score=row["odds_exact_score"],
            )
        self._cache[guild_id] = settings
        return settings

    async def update(self, guild_id: int, **values) -> GuildSettings:
        unknown = set(values) - _EDITABLE
        if unknown:
            raise ValueError(f"Paramètres inconnus : {unknown}")
        await self.get(guild_id)  # garantit l'existence de la ligne
        if "reminder_offsets" in values:
            values["reminder_offsets"] = json.dumps(sorted(set(values["reminder_offsets"]), reverse=True))
        assignments = ", ".join(f"{k} = :{k}" for k in values)
        await self.db.execute(
            f"UPDATE guild_settings SET {assignments} WHERE guild_id = :guild_id",
            {**values, "guild_id": guild_id},
        )
        self._cache.pop(guild_id, None)
        return await self.get(guild_id)
