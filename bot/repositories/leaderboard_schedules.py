"""Classements publiés automatiquement (table ``leaderboard_schedules``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import aiosqlite

from bot.db import Database
from bot.repositories.points import LeaderboardFilter
from bot.utils.time import from_db, to_db

FREQ_DAILY = "daily"
FREQ_WEEKLY = "weekly"
FREQUENCY_LABELS = {FREQ_DAILY: "Tous les jours", FREQ_WEEKLY: "Chaque semaine"}


@dataclass(slots=True)
class LeaderboardSchedule:
    id: int
    guild_id: int
    channel_id: int
    period: str
    competition_id: int | None
    tournament_name: str | None
    bet_type: str | None
    frequency: str
    weekday: int
    hour: int
    last_posted_at: datetime | None

    @property
    def filter(self) -> LeaderboardFilter:
        return LeaderboardFilter(
            period=self.period,
            competition_id=self.competition_id,
            tournament_name=self.tournament_name,
            bet_type=self.bet_type,
        )

    @classmethod
    def from_row(cls, r: aiosqlite.Row) -> "LeaderboardSchedule":
        return cls(
            id=r["id"],
            guild_id=r["guild_id"],
            channel_id=r["channel_id"],
            period=r["period"],
            competition_id=r["competition_id"],
            tournament_name=r["tournament_name"],
            bet_type=r["bet_type"],
            frequency=r["frequency"],
            weekday=r["weekday"],
            hour=r["hour"],
            last_posted_at=from_db(r["last_posted_at"]),
        )


class LeaderboardScheduleRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        guild_id: int,
        *,
        channel_id: int,
        frequency: str,
        weekday: int,
        hour: int,
        period: str,
        competition_id: int | None = None,
        tournament_name: str | None = None,
        bet_type: str | None = None,
        last_posted_at: datetime | None = None,
    ) -> LeaderboardSchedule:
        schedule_id = await self.db.execute(
            """INSERT INTO leaderboard_schedules (guild_id, channel_id, period, competition_id,
                   tournament_name, bet_type, frequency, weekday, hour, last_posted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, channel_id, period, competition_id, tournament_name, bet_type, frequency,
             weekday, hour, to_db(last_posted_at) if last_posted_at else None),
        )
        return await self.get(schedule_id)  # type: ignore[return-value]

    async def get(self, schedule_id: int) -> LeaderboardSchedule | None:
        r = await self.db.fetchone("SELECT * FROM leaderboard_schedules WHERE id = ?", (schedule_id,))
        return LeaderboardSchedule.from_row(r) if r else None

    async def list(self, guild_id: int) -> list[LeaderboardSchedule]:
        rows = await self.db.fetchall(
            "SELECT * FROM leaderboard_schedules WHERE guild_id = ? ORDER BY id", (guild_id,)
        )
        return [LeaderboardSchedule.from_row(r) for r in rows]

    async def list_all(self) -> list[LeaderboardSchedule]:
        rows = await self.db.fetchall("SELECT * FROM leaderboard_schedules ORDER BY id")
        return [LeaderboardSchedule.from_row(r) for r in rows]

    async def delete(self, guild_id: int, schedule_id: int) -> bool:
        return bool(await self.db.execute_rowcount(
            "DELETE FROM leaderboard_schedules WHERE id = ? AND guild_id = ?", (schedule_id, guild_id)
        ))

    async def mark_posted(self, schedule_id: int, when: datetime) -> None:
        await self.db.execute(
            "UPDATE leaderboard_schedules SET last_posted_at = ? WHERE id = ?", (to_db(when), schedule_id)
        )
