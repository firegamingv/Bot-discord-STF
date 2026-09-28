"""Association compte Discord <-> compte Riot (PUUID) (table ``riot_accounts``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import aiosqlite

from bot.db import Database
from bot.repositories.users import UserRepository
from bot.utils.time import from_db, now_utc, to_db

TIER_ORDER = [
    "IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD",
    "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER",
]
TIER_LABELS = {
    "IRON": "Fer", "BRONZE": "Bronze", "SILVER": "Argent", "GOLD": "Or",
    "PLATINUM": "Platine", "EMERALD": "Émeraude", "DIAMOND": "Diamant",
    "MASTER": "Maître", "GRANDMASTER": "Grand Maître", "CHALLENGER": "Challenger",
}
DIVISION_ORDER = {"IV": 0, "III": 1, "II": 2, "I": 3}


@dataclass(slots=True)
class RiotAccount:
    discord_id: int
    puuid: str | None
    game_name: str
    tag_line: str
    platform: str | None
    rank_tier: str | None
    rank_division: str | None
    league_points: int | None
    rank_updated_at: datetime | None

    @property
    def riot_id(self) -> str:
        return f"{self.game_name}#{self.tag_line}"

    @property
    def verified(self) -> bool:
        return self.puuid is not None

    @property
    def rank_label(self) -> str:
        if not self.rank_tier:
            return "Non classé"
        label = TIER_LABELS.get(self.rank_tier, self.rank_tier.title())
        if self.rank_tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
            return f"{label} {self.league_points or 0} LP"
        return f"{label} {self.rank_division or ''} ({self.league_points or 0} LP)".replace("  ", " ")

    @property
    def rank_score(self) -> float | None:
        """Valeur numérique du rang pour équilibrer les équipes (None = inconnu).

        Fer IV 0 LP = 0 ; chaque division vaut 100 ; Maître+ = 2800 + LP.
        """
        if not self.rank_tier or self.rank_tier not in TIER_ORDER:
            return None
        tier_idx = TIER_ORDER.index(self.rank_tier)
        lp = self.league_points or 0
        if tier_idx >= TIER_ORDER.index("MASTER"):
            return 2800 + lp
        return tier_idx * 400 + DIVISION_ORDER.get(self.rank_division or "IV", 0) * 100 + min(lp, 100)

    @classmethod
    def from_row(cls, r: aiosqlite.Row) -> "RiotAccount":
        return cls(
            discord_id=r["discord_id"],
            puuid=r["puuid"],
            game_name=r["game_name"],
            tag_line=r["tag_line"],
            platform=r["platform"],
            rank_tier=r["rank_tier"],
            rank_division=r["rank_division"],
            league_points=r["league_points"],
            rank_updated_at=from_db(r["rank_updated_at"]),
        )


class RiotAccountRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, discord_id: int) -> RiotAccount | None:
        r = await self.db.fetchone("SELECT * FROM riot_accounts WHERE discord_id = ?", (discord_id,))
        return RiotAccount.from_row(r) if r else None

    async def get_many(self, discord_ids: list[int]) -> dict[int, RiotAccount]:
        if not discord_ids:
            return {}
        placeholders = ",".join("?" * len(discord_ids))
        rows = await self.db.fetchall(
            f"SELECT * FROM riot_accounts WHERE discord_id IN ({placeholders})", discord_ids
        )
        return {r["discord_id"]: RiotAccount.from_row(r) for r in rows}

    async def get_by_puuid(self, puuid: str) -> RiotAccount | None:
        r = await self.db.fetchone("SELECT * FROM riot_accounts WHERE puuid = ?", (puuid,))
        return RiotAccount.from_row(r) if r else None

    async def link(
        self,
        discord_id: int,
        *,
        puuid: str | None,
        game_name: str,
        tag_line: str,
        platform: str | None,
        display_name: str | None = None,
    ) -> RiotAccount:
        await UserRepository(self.db).ensure(discord_id, display_name)
        await self.db.execute(
            """INSERT INTO riot_accounts (discord_id, puuid, game_name, tag_line, platform)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(discord_id) DO UPDATE SET
                   puuid = excluded.puuid, game_name = excluded.game_name,
                   tag_line = excluded.tag_line, platform = excluded.platform,
                   rank_tier = NULL, rank_division = NULL, league_points = NULL,
                   rank_updated_at = NULL,
                   linked_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')""",
            (discord_id, puuid, game_name, tag_line, platform),
        )
        return await self.get(discord_id)  # type: ignore[return-value]

    async def update_rank(
        self,
        discord_id: int,
        *,
        tier: str | None,
        division: str | None,
        league_points: int | None,
        game_name: str | None = None,
        tag_line: str | None = None,
    ) -> None:
        await self.db.execute(
            """UPDATE riot_accounts SET rank_tier = ?, rank_division = ?, league_points = ?,
                   rank_updated_at = ?,
                   game_name = COALESCE(?, game_name), tag_line = COALESCE(?, tag_line)
               WHERE discord_id = ?""",
            (tier, division, league_points, to_db(now_utc()), game_name, tag_line, discord_id),
        )

    async def unlink(self, discord_id: int) -> bool:
        return bool(
            await self.db.execute_rowcount("DELETE FROM riot_accounts WHERE discord_id = ?", (discord_id,))
        )
