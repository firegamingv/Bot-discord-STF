"""Matchs esport sur lesquels on peut parier (table ``matches``).

États : ``upcoming`` (paris ouverts jusqu'à l'heure de début), ``live``, ``completed``,
``cancelled``. ``settled`` = les paris du match ont été réglés (garantit l'idempotence).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import aiosqlite

from bot.db import Database
from bot.utils.time import from_db, now_utc, to_db

STATE_UPCOMING = "upcoming"
STATE_LIVE = "live"
STATE_COMPLETED = "completed"
STATE_CANCELLED = "cancelled"

STATE_LABELS = {
    STATE_UPCOMING: "🟢 Paris ouverts",
    STATE_LIVE: "🔴 En cours",
    STATE_COMPLETED: "🏁 Terminé",
    STATE_CANCELLED: "🚫 Annulé",
}

# État API LoL Esports → état en base
API_STATE_MAP = {"unstarted": STATE_UPCOMING, "inProgress": STATE_LIVE, "completed": STATE_COMPLETED}


@dataclass(slots=True)
class Match:
    id: int
    guild_id: int
    competition_id: int
    external_id: str
    tournament_name: str | None
    block_name: str | None
    team1_name: str
    team1_code: str | None
    team2_name: str
    team2_code: str | None
    best_of: int
    starts_at: datetime
    state: str
    team1_score: int | None
    team2_score: int | None
    winner: int | None
    settled: bool

    def is_open(self, now: datetime | None = None) -> bool:
        """Paris ouverts : match à venir et heure de début pas encore atteinte."""
        return self.state == STATE_UPCOMING and self.starts_at > (now or now_utc())

    def team_name(self, team: int | str) -> str:
        return self.team1_name if str(team) == "1" else self.team2_name

    def team_code(self, team: int | str) -> str:
        if str(team) == "1":
            return self.team1_code or self.team1_name
        return self.team2_code or self.team2_name

    @property
    def title(self) -> str:
        return f"{self.team1_name} vs {self.team2_name}"

    @property
    def short_title(self) -> str:
        return f"{self.team_code(1)} vs {self.team_code(2)}"

    @property
    def score_text(self) -> str | None:
        if self.team1_score is None or self.team2_score is None:
            return None
        return f"{self.team1_score}-{self.team2_score}"

    @classmethod
    def from_row(cls, r: aiosqlite.Row) -> "Match":
        return cls(
            id=r["id"],
            guild_id=r["guild_id"],
            competition_id=r["competition_id"],
            external_id=r["external_id"],
            tournament_name=r["tournament_name"],
            block_name=r["block_name"],
            team1_name=r["team1_name"],
            team1_code=r["team1_code"],
            team2_name=r["team2_name"],
            team2_code=r["team2_code"],
            best_of=r["best_of"],
            starts_at=from_db(r["starts_at"]),  # type: ignore[arg-type]
            state=r["state"],
            team1_score=r["team1_score"],
            team2_score=r["team2_score"],
            winner=r["winner"],
            settled=bool(r["settled"]),
        )


class MatchRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ lecture
    async def get(self, match_id: int) -> Match | None:
        r = await self.db.fetchone("SELECT * FROM matches WHERE id = ?", (match_id,))
        return Match.from_row(r) if r else None

    async def get_in_guild(self, guild_id: int, match_id: int) -> Match | None:
        r = await self.db.fetchone(
            "SELECT * FROM matches WHERE id = ? AND guild_id = ?", (match_id, guild_id)
        )
        return Match.from_row(r) if r else None

    async def get_by_external(self, guild_id: int, external_id: str) -> Match | None:
        r = await self.db.fetchone(
            "SELECT * FROM matches WHERE guild_id = ? AND external_id = ?", (guild_id, external_id)
        )
        return Match.from_row(r) if r else None

    async def list_between(
        self,
        guild_id: int,
        start: datetime,
        end: datetime,
        *,
        competition_id: int | None = None,
        states: tuple[str, ...] | None = None,
        followed_only: bool = True,
        limit: int = 100,
    ) -> list[Match]:
        """Matchs dont le début est dans ``[start, end[``, par ordre chronologique."""
        sql = """SELECT m.* FROM matches m JOIN competitions c ON c.id = m.competition_id
                 WHERE m.guild_id = ? AND m.starts_at >= ? AND m.starts_at < ?"""
        params: list = [guild_id, to_db(start), to_db(end)]
        if followed_only:
            sql += " AND c.followed = 1"
        if competition_id is not None:
            sql += " AND m.competition_id = ?"
            params.append(competition_id)
        if states:
            sql += f" AND m.state IN ({','.join('?' * len(states))})"
            params.extend(states)
        sql += " ORDER BY m.starts_at ASC, m.id ASC LIMIT ?"
        params.append(limit)
        return [Match.from_row(r) for r in await self.db.fetchall(sql, params)]

    async def search(
        self,
        guild_id: int,
        text: str,
        *,
        bettable_only: bool = False,
        unsettled_only: bool = False,
        competition_id: int | None = None,
        limit: int = 25,
    ) -> list[Match]:
        """Pour l'autocomplétion : recherche sur les équipes, codes, tournoi ou ID."""
        pattern = f"%{text.strip()}%"
        sql = """SELECT m.* FROM matches m JOIN competitions c ON c.id = m.competition_id
                 WHERE m.guild_id = ?
                   AND (m.team1_name LIKE ? OR m.team2_name LIKE ? OR m.team1_code LIKE ?
                        OR m.team2_code LIKE ? OR m.tournament_name LIKE ? OR c.name LIKE ?
                        OR CAST(m.id AS TEXT) = ?)"""
        params: list = [guild_id, *([pattern] * 6), text.strip().lstrip("#")]
        if bettable_only:
            sql += " AND m.state = ? AND m.starts_at > ? AND c.followed = 1"
            params.extend([STATE_UPCOMING, to_db(now_utc())])
            order = "m.starts_at ASC"
        elif unsettled_only:
            sql += " AND m.settled = 0 AND m.state != ?"
            params.append(STATE_CANCELLED)
            order = "m.starts_at ASC"
        else:
            order = "m.starts_at DESC"
        if competition_id is not None:
            sql += " AND m.competition_id = ?"
            params.append(competition_id)
        sql += f" ORDER BY {order} LIMIT ?"
        params.append(limit)
        return [Match.from_row(r) for r in await self.db.fetchall(sql, params)]

    async def list_to_settle(self) -> list[Match]:
        """Matchs terminés (avec vainqueur ou score) mais pas encore réglés, tous serveurs."""
        rows = await self.db.fetchall(
            "SELECT * FROM matches WHERE settled = 0 AND state IN (?, ?) ORDER BY starts_at",
            (STATE_COMPLETED, STATE_CANCELLED),
        )
        return [Match.from_row(r) for r in rows]

    async def list_tournaments(
        self, guild_id: int, *, competition_id: int | None = None, text: str = "", limit: int = 25
    ) -> list[str]:
        sql = """SELECT tournament_name, MAX(starts_at) AS last FROM matches
                 WHERE guild_id = ? AND tournament_name IS NOT NULL AND tournament_name LIKE ?"""
        params: list = [guild_id, f"%{text}%"]
        if competition_id is not None:
            sql += " AND competition_id = ?"
            params.append(competition_id)
        sql += " GROUP BY tournament_name ORDER BY last DESC LIMIT ?"
        params.append(limit)
        return [r["tournament_name"] for r in await self.db.fetchall(sql, params)]

    # ------------------------------------------------------------------ écriture
    async def upsert_external(
        self,
        guild_id: int,
        competition_id: int,
        *,
        external_id: str,
        tournament_name: str | None,
        block_name: str | None,
        team1_name: str,
        team1_code: str | None,
        team2_name: str,
        team2_code: str | None,
        best_of: int,
        starts_at: datetime,
        state: str,
        team1_score: int | None,
        team2_score: int | None,
        winner: int | None,
    ) -> tuple[Match, Match | None]:
        """Crée ou met à jour un match importé. Renvoie ``(match, ancien_état_ou_None)``.

        Un match déjà réglé ou annulé n'est plus modifié (hormis les noms d'équipes).
        """
        previous = await self.get_by_external(guild_id, external_id)
        if previous is None:
            match_id = await self.db.execute(
                """INSERT INTO matches (guild_id, competition_id, external_id, tournament_name, block_name,
                       team1_name, team1_code, team2_name, team2_code, best_of, starts_at, state,
                       team1_score, team2_score, winner)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (guild_id, competition_id, external_id, tournament_name, block_name,
                 team1_name, team1_code, team2_name, team2_code, best_of, to_db(starts_at), state,
                 team1_score, team2_score, winner),
            )
            return (await self.get(match_id)), None  # type: ignore[return-value]
        if previous.settled or previous.state == STATE_CANCELLED:
            return previous, previous
        await self.db.execute(
            """UPDATE matches SET tournament_name = COALESCE(?, tournament_name), block_name = ?,
                   team1_name = ?, team1_code = ?, team2_name = ?, team2_code = ?, best_of = ?,
                   starts_at = ?, state = ?, team1_score = ?, team2_score = ?, winner = ?
               WHERE id = ?""",
            (tournament_name, block_name, team1_name, team1_code, team2_name, team2_code, best_of,
             to_db(starts_at), state, team1_score, team2_score, winner, previous.id),
        )
        return (await self.get(previous.id)), previous  # type: ignore[return-value]

    async def create_manual(
        self,
        guild_id: int,
        competition_id: int,
        *,
        team1_name: str,
        team2_name: str,
        starts_at: datetime,
        best_of: int = 1,
        tournament_name: str | None = None,
        team1_code: str | None = None,
        team2_code: str | None = None,
    ) -> Match:
        match_id = await self.db.execute(
            """INSERT INTO matches (guild_id, competition_id, external_id, tournament_name,
                   team1_name, team1_code, team2_name, team2_code, best_of, starts_at, state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, competition_id, f"manual-{uuid.uuid4().hex[:12]}", tournament_name,
             team1_name, team1_code, team2_name, team2_code, best_of, to_db(starts_at), STATE_UPCOMING),
        )
        return await self.get(match_id)  # type: ignore[return-value]

    async def set_result(
        self, match_id: int, *, team1_score: int | None, team2_score: int | None, winner: int | None,
        state: str = STATE_COMPLETED,
    ) -> None:
        await self.db.execute(
            "UPDATE matches SET state = ?, team1_score = ?, team2_score = ?, winner = ? WHERE id = ?",
            (state, team1_score, team2_score, winner, match_id),
        )

    async def set_state(self, match_id: int, state: str) -> None:
        await self.db.execute("UPDATE matches SET state = ? WHERE id = ?", (state, match_id))

    async def mark_live_started(self, guild_id: int | None = None) -> int:
        """Passe en ``live`` les matchs manuels dont l'heure est passée (ferme les paris)."""
        sql = "UPDATE matches SET state = ? WHERE state = ? AND starts_at <= ? AND external_id LIKE 'manual-%'"
        params: list = [STATE_LIVE, STATE_UPCOMING, to_db(now_utc())]
        if guild_id is not None:
            sql += " AND guild_id = ?"
            params.append(guild_id)
        return await self.db.execute_rowcount(sql, params)

    @staticmethod
    async def mark_settled(conn: aiosqlite.Connection, match_id: int) -> bool:
        """À appeler dans une transaction. ``False`` si le match était déjà réglé."""
        cur = await conn.execute(
            "UPDATE matches SET settled = 1 WHERE id = ? AND settled = 0", (match_id,)
        )
        return cur.rowcount > 0
