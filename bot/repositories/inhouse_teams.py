"""Équipes générées pour un inhouse (table ``inhouse_team_members``).

Les remplaçants sont stockés dans la même table avec ``team_index = -1``
(``SUBSTITUTES_INDEX``) pour conserver toute la composition au même endroit.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from bot.db import Database
from bot.utils.time import now_utc, to_db

SUBSTITUTES_INDEX = -1


@dataclass(slots=True)
class TeamMember:
    event_id: int
    team_index: int
    discord_id: int
    assigned_role: str | None

    @property
    def is_substitute(self) -> bool:
        return self.team_index == SUBSTITUTES_INDEX


@dataclass(slots=True)
class StoredTeams:
    """Composition enregistrée. ``teams[i]`` = membres de l'équipe d'index ``i``."""

    event_id: int
    teams: dict[int, list[TeamMember]] = field(default_factory=dict)
    substitutes: list[int] = field(default_factory=list)

    @property
    def exists(self) -> bool:
        return bool(self.teams)

    @property
    def team_count(self) -> int:
        return (max(self.teams) + 1) if self.teams else 0

    def team(self, index: int) -> list[TeamMember]:
        return self.teams.get(index, [])

    def find(self, discord_id: int) -> TeamMember | None:
        for members in self.teams.values():
            for m in members:
                if m.discord_id == discord_id:
                    return m
        if discord_id in self.substitutes:
            return TeamMember(self.event_id, SUBSTITUTES_INDEX, discord_id, None)
        return None

    def all_player_ids(self) -> list[int]:
        return [m.discord_id for i in sorted(self.teams) for m in self.teams[i]]

    def as_lists(self) -> list[list[tuple[int, str | None]]]:
        """Format accepté par ``save_teams`` (pour restaurer une composition)."""
        return [
            [(m.discord_id, m.assigned_role) for m in self.teams.get(i, [])]
            for i in range(self.team_count)
        ]


class InhouseTeamRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def save_teams(
        self,
        event_id: int,
        teams: Sequence[Sequence[tuple[int, str | None]]],
        substitutes: Iterable[int] = (),
    ) -> None:
        """Remplace TOUTE la composition de l'événement (transaction atomique).

        ``teams[i]`` = liste de ``(discord_id, rôle attribué ou None)`` de l'équipe ``i``.
        Met aussi à jour ``inhouse_sessions.teams_generated_at``.
        """
        rows: list[tuple[int, int, int, str | None]] = []
        seen: set[int] = set()
        for index, members in enumerate(teams):
            for discord_id, role in members:
                if discord_id in seen:
                    raise ValueError(f"Joueur {discord_id} présent dans plusieurs équipes")
                seen.add(discord_id)
                rows.append((event_id, index, discord_id, role))
        for discord_id in substitutes:
            if discord_id not in seen:
                seen.add(discord_id)
                rows.append((event_id, SUBSTITUTES_INDEX, discord_id, None))

        async with self.db.transaction() as conn:
            await conn.execute("DELETE FROM inhouse_team_members WHERE event_id = ?", (event_id,))
            await conn.executemany(
                "INSERT INTO inhouse_team_members (event_id, team_index, discord_id, assigned_role) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )
            await conn.execute(
                "UPDATE inhouse_sessions SET teams_generated_at = ? WHERE event_id = ?",
                (to_db(now_utc()) if rows else None, event_id),
            )

    async def get_teams(self, event_id: int) -> StoredTeams:
        rows = await self.db.fetchall(
            "SELECT * FROM inhouse_team_members WHERE event_id = ? ORDER BY team_index, rowid",
            (event_id,),
        )
        result = StoredTeams(event_id=event_id)
        for r in rows:
            if r["team_index"] == SUBSTITUTES_INDEX:
                result.substitutes.append(r["discord_id"])
            else:
                result.teams.setdefault(r["team_index"], []).append(
                    TeamMember(event_id, r["team_index"], r["discord_id"], r["assigned_role"])
                )
        return result

    async def has_teams(self, event_id: int) -> bool:
        return bool(
            await self.db.fetchval(
                "SELECT COUNT(*) FROM inhouse_team_members WHERE event_id = ? AND team_index >= 0",
                (event_id,),
            )
        )

    async def get_member(self, event_id: int, discord_id: int) -> TeamMember | None:
        r = await self.db.fetchone(
            "SELECT * FROM inhouse_team_members WHERE event_id = ? AND discord_id = ?",
            (event_id, discord_id),
        )
        return TeamMember(event_id, r["team_index"], r["discord_id"], r["assigned_role"]) if r else None

    async def move_player(
        self, event_id: int, discord_id: int, team_index: int, role: str | None = None
    ) -> None:
        """Place le joueur dans l'équipe ``team_index`` (``-1`` = remplaçants) avec ``role``.

        Le joueur est ajouté s'il n'était pas encore dans la composition.
        """
        if team_index == SUBSTITUTES_INDEX:
            role = None
        await self.db.execute(
            """INSERT INTO inhouse_team_members (event_id, team_index, discord_id, assigned_role)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(event_id, discord_id) DO UPDATE SET
                   team_index = excluded.team_index, assigned_role = excluded.assigned_role""",
            (event_id, team_index, discord_id, role),
        )

    async def swap_players(self, event_id: int, discord_id_a: int, discord_id_b: int) -> None:
        """Échange équipe ET rôle de deux joueurs. Lève ``LookupError`` si l'un est absent."""
        a = await self.get_member(event_id, discord_id_a)
        b = await self.get_member(event_id, discord_id_b)
        if a is None or b is None:
            missing = discord_id_a if a is None else discord_id_b
            raise LookupError(f"Joueur {missing} absent de la composition")
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE inhouse_team_members SET team_index = ?, assigned_role = ? "
                "WHERE event_id = ? AND discord_id = ?",
                (b.team_index, b.assigned_role, event_id, a.discord_id),
            )
            await conn.execute(
                "UPDATE inhouse_team_members SET team_index = ?, assigned_role = ? "
                "WHERE event_id = ? AND discord_id = ?",
                (a.team_index, a.assigned_role, event_id, b.discord_id),
            )

    async def remove_player(self, event_id: int, discord_id: int) -> TeamMember | None:
        """Retire le joueur de la composition. Renvoie sa place d'avant (ou None)."""
        member = await self.get_member(event_id, discord_id)
        if member is None:
            return None
        await self.db.execute(
            "DELETE FROM inhouse_team_members WHERE event_id = ? AND discord_id = ?",
            (event_id, discord_id),
        )
        return member

    async def clear(self, event_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute("DELETE FROM inhouse_team_members WHERE event_id = ?", (event_id,))
            await conn.execute(
                "UPDATE inhouse_sessions SET teams_generated_at = NULL WHERE event_id = ?", (event_id,)
            )
