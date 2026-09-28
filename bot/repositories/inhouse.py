"""Sessions d'inhouse (table ``inhouse_sessions``) : extension d'un événement de type ``inhouse``.

Une session = une ligne dans ``events`` (titre, date, places, inscriptions…) + une ligne
ici pour ce qui est propre à l'inhouse (mode de jeu, message des équipes publiées).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import aiosqlite

from bot.db import Database
from bot.utils.time import from_db, now_utc, to_db

GAME_MODES = ("sr", "aram", "arena")


@dataclass(slots=True)
class InhouseSession:
    event_id: int
    game_mode: str
    teams_channel_id: int | None
    teams_message_id: int | None
    teams_generated_at: datetime | None

    @property
    def teams_published(self) -> bool:
        return self.teams_message_id is not None

    @classmethod
    def from_row(cls, r: aiosqlite.Row) -> "InhouseSession":
        return cls(
            event_id=r["event_id"],
            game_mode=r["game_mode"],
            teams_channel_id=r["teams_channel_id"],
            teams_message_id=r["teams_message_id"],
            teams_generated_at=from_db(r["teams_generated_at"]),
        )


def _check_mode(mode: str) -> None:
    if mode not in GAME_MODES:
        raise ValueError(f"Mode de jeu inconnu : {mode!r} (attendu : {', '.join(GAME_MODES)})")


class InhouseRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, event_id: int, game_mode: str) -> InhouseSession:
        """Crée (ou remplace le mode de) la session rattachée à l'événement ``event_id``."""
        _check_mode(game_mode)
        await self.db.execute(
            """INSERT INTO inhouse_sessions (event_id, game_mode) VALUES (?, ?)
               ON CONFLICT(event_id) DO UPDATE SET game_mode = excluded.game_mode""",
            (event_id, game_mode),
        )
        return await self.get(event_id)  # type: ignore[return-value]

    async def get(self, event_id: int) -> InhouseSession | None:
        r = await self.db.fetchone("SELECT * FROM inhouse_sessions WHERE event_id = ?", (event_id,))
        return InhouseSession.from_row(r) if r else None

    async def get_many(self, event_ids: list[int]) -> dict[int, InhouseSession]:
        if not event_ids:
            return {}
        placeholders = ",".join("?" * len(event_ids))
        rows = await self.db.fetchall(
            f"SELECT * FROM inhouse_sessions WHERE event_id IN ({placeholders})", event_ids
        )
        return {r["event_id"]: InhouseSession.from_row(r) for r in rows}

    async def update_mode(self, event_id: int, game_mode: str) -> InhouseSession | None:
        _check_mode(game_mode)
        await self.db.execute(
            "UPDATE inhouse_sessions SET game_mode = ? WHERE event_id = ?", (game_mode, event_id)
        )
        return await self.get(event_id)

    async def set_teams_message(
        self, event_id: int, channel_id: int | None, message_id: int | None
    ) -> None:
        """Mémorise le message public des équipes (``None, None`` pour l'oublier)."""
        await self.db.execute(
            "UPDATE inhouse_sessions SET teams_channel_id = ?, teams_message_id = ? WHERE event_id = ?",
            (channel_id, message_id, event_id),
        )

    async def clear_teams_message(self, event_id: int) -> None:
        await self.set_teams_message(event_id, None, None)

    async def mark_teams_generated(self, event_id: int, when: datetime | None = None) -> None:
        await self.db.execute(
            "UPDATE inhouse_sessions SET teams_generated_at = ? WHERE event_id = ?",
            (to_db(when or now_utc()), event_id),
        )

    async def clear_teams_generated(self, event_id: int) -> None:
        await self.db.execute(
            "UPDATE inhouse_sessions SET teams_generated_at = NULL WHERE event_id = ?", (event_id,)
        )

    async def delete(self, event_id: int) -> None:
        await self.db.execute("DELETE FROM inhouse_sessions WHERE event_id = ?", (event_id,))
