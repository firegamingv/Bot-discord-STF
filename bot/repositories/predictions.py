"""Paris des membres (table ``predictions``).

Un pari par (match, membre, type de pari). Les méthodes d'écriture prennent une connexion
``conn`` : elles sont faites pour être appelées dans ``db.transaction()`` avec les
mouvements de points correspondants (voir ``features/predictions/betting_service.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import aiosqlite

from bot.db import Database
from bot.utils.time import from_db, now_utc, to_db

STATUS_PENDING = "pending"
STATUS_WON = "won"
STATUS_LOST = "lost"
STATUS_REFUNDED = "refunded"

STATUS_LABELS = {
    STATUS_PENDING: "⏳ En cours",
    STATUS_WON: "✅ Gagné",
    STATUS_LOST: "❌ Perdu",
    STATUS_REFUNDED: "↩️ Remboursé",
}


@dataclass(slots=True)
class Prediction:
    id: int
    guild_id: int
    match_id: int
    discord_id: int
    bet_type: str
    choice: str
    stake: int
    odds: float
    status: str
    payout: int
    created_at: datetime
    settled_at: datetime | None

    @property
    def potential_payout(self) -> int:
        from bot.services.betting_rules import compute_payout

        return compute_payout(self.stake, self.odds)

    @property
    def net(self) -> int:
        """Gain net une fois réglé (payout − mise) ; 0 si remboursé ou en cours."""
        if self.status in (STATUS_PENDING, STATUS_REFUNDED):
            return 0
        return self.payout - self.stake

    @classmethod
    def from_row(cls, r: aiosqlite.Row) -> "Prediction":
        return cls(
            id=r["id"],
            guild_id=r["guild_id"],
            match_id=r["match_id"],
            discord_id=r["discord_id"],
            bet_type=r["bet_type"],
            choice=r["choice"],
            stake=r["stake"],
            odds=r["odds"],
            status=r["status"],
            payout=r["payout"],
            created_at=from_db(r["created_at"]),  # type: ignore[arg-type]
            settled_at=from_db(r["settled_at"]),
        )


class PredictionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ lecture
    async def get(self, prediction_id: int) -> Prediction | None:
        r = await self.db.fetchone("SELECT * FROM predictions WHERE id = ?", (prediction_id,))
        return Prediction.from_row(r) if r else None

    async def get_user_bet(self, match_id: int, discord_id: int, bet_type: str) -> Prediction | None:
        r = await self.db.fetchone(
            "SELECT * FROM predictions WHERE match_id = ? AND discord_id = ? AND bet_type = ?",
            (match_id, discord_id, bet_type),
        )
        return Prediction.from_row(r) if r else None

    async def list_user_bets_for_match(self, match_id: int, discord_id: int) -> list[Prediction]:
        rows = await self.db.fetchall(
            "SELECT * FROM predictions WHERE match_id = ? AND discord_id = ? ORDER BY bet_type",
            (match_id, discord_id),
        )
        return [Prediction.from_row(r) for r in rows]

    async def list_for_match(self, match_id: int, *, status: str | None = None) -> list[Prediction]:
        sql = "SELECT * FROM predictions WHERE match_id = ?"
        params: list = [match_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at, id"
        return [Prediction.from_row(r) for r in await self.db.fetchall(sql, params)]

    async def list_for_user(
        self, guild_id: int, discord_id: int, *, statuses: tuple[str, ...] | None = None, limit: int = 25
    ) -> list[Prediction]:
        sql = "SELECT * FROM predictions WHERE guild_id = ? AND discord_id = ?"
        params: list = [guild_id, discord_id]
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            params.extend(statuses)
        sql += " ORDER BY COALESCE(settled_at, created_at) DESC, id DESC LIMIT ?"
        params.append(limit)
        return [Prediction.from_row(r) for r in await self.db.fetchall(sql, params)]

    async def count_by_choice(self, match_id: int) -> dict[tuple[str, str], int]:
        """Nombre de paris par (type, choix) : ``{("winner", "1"): 4, ("exact_score", "2-1"): 1}``."""
        rows = await self.db.fetchall(
            "SELECT bet_type, choice, COUNT(*) AS n FROM predictions WHERE match_id = ? "
            "AND status != ? GROUP BY bet_type, choice",
            (match_id, STATUS_REFUNDED),
        )
        return {(r["bet_type"], r["choice"]): r["n"] for r in rows}

    async def total_staked(self, match_id: int) -> int:
        return await self.db.fetchval(
            "SELECT COALESCE(SUM(stake), 0) FROM predictions WHERE match_id = ? AND status = ?",
            (match_id, STATUS_PENDING),
        )

    # ------------------------------------------------------------------ écriture (transaction)
    @staticmethod
    async def insert(
        conn: aiosqlite.Connection,
        *,
        guild_id: int,
        match_id: int,
        discord_id: int,
        bet_type: str,
        choice: str,
        stake: int,
        odds: float,
    ) -> int:
        cur = await conn.execute(
            """INSERT INTO predictions (guild_id, match_id, discord_id, bet_type, choice, stake, odds, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, match_id, discord_id, bet_type, choice, stake, odds, to_db(now_utc())),
        )
        return cur.lastrowid  # type: ignore[return-value]

    @staticmethod
    async def update_pending(
        conn: aiosqlite.Connection, prediction_id: int, *, choice: str, stake: int, odds: float
    ) -> None:
        await conn.execute(
            "UPDATE predictions SET choice = ?, stake = ?, odds = ?, created_at = ? WHERE id = ? AND status = ?",
            (choice, stake, odds, to_db(now_utc()), prediction_id, STATUS_PENDING),
        )

    @staticmethod
    async def settle(conn: aiosqlite.Connection, prediction_id: int, *, status: str, payout: int) -> None:
        await conn.execute(
            "UPDATE predictions SET status = ?, payout = ?, settled_at = ? WHERE id = ? AND status = ?",
            (status, payout, to_db(now_utc()), prediction_id, STATUS_PENDING),
        )
