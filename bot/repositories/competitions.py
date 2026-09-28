"""Compétitions suivies par serveur (table ``competitions``).

Une compétition vient soit de LoL Esports (``source='lolesports'``, ``external_id`` = ID de
ligue), soit d'une création manuelle par un organisateur (``source='manual'``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import aiosqlite

from bot.db import Database

SOURCE_LOLESPORTS = "lolesports"
SOURCE_MANUAL = "manual"


@dataclass(slots=True)
class Competition:
    id: int
    guild_id: int
    source: str
    external_id: str
    name: str
    slug: str | None
    image_url: str | None
    followed: bool

    @property
    def is_manual(self) -> bool:
        return self.source == SOURCE_MANUAL

    @classmethod
    def from_row(cls, r: aiosqlite.Row) -> "Competition":
        return cls(
            id=r["id"],
            guild_id=r["guild_id"],
            source=r["source"],
            external_id=r["external_id"],
            name=r["name"],
            slug=r["slug"],
            image_url=r["image_url"],
            followed=bool(r["followed"]),
        )


def slugify(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-") or "competition"


class CompetitionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, competition_id: int) -> Competition | None:
        r = await self.db.fetchone("SELECT * FROM competitions WHERE id = ?", (competition_id,))
        return Competition.from_row(r) if r else None

    async def get_in_guild(self, guild_id: int, competition_id: int) -> Competition | None:
        r = await self.db.fetchone(
            "SELECT * FROM competitions WHERE id = ? AND guild_id = ?", (competition_id, guild_id)
        )
        return Competition.from_row(r) if r else None

    async def get_by_external(self, guild_id: int, source: str, external_id: str) -> Competition | None:
        r = await self.db.fetchone(
            "SELECT * FROM competitions WHERE guild_id = ? AND source = ? AND external_id = ?",
            (guild_id, source, external_id),
        )
        return Competition.from_row(r) if r else None

    async def list(self, guild_id: int, *, followed_only: bool = True, source: str | None = None) -> list[Competition]:
        sql = "SELECT * FROM competitions WHERE guild_id = ?"
        params: list = [guild_id]
        if followed_only:
            sql += " AND followed = 1"
        if source is not None:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY name COLLATE NOCASE"
        return [Competition.from_row(r) for r in await self.db.fetchall(sql, params)]

    async def list_followed_all_guilds(self, source: str = SOURCE_LOLESPORTS) -> list[Competition]:
        """Pour la synchronisation : toutes les compétitions suivies, tous serveurs confondus."""
        rows = await self.db.fetchall(
            "SELECT * FROM competitions WHERE followed = 1 AND source = ? ORDER BY guild_id, id",
            (source,),
        )
        return [Competition.from_row(r) for r in rows]

    async def search(self, guild_id: int, text: str, *, followed_only: bool = True, limit: int = 25) -> list[Competition]:
        sql = "SELECT * FROM competitions WHERE guild_id = ? AND (name LIKE ? OR slug LIKE ?)"
        params: list = [guild_id, f"%{text}%", f"%{text}%"]
        if followed_only:
            sql += " AND followed = 1"
        sql += " ORDER BY name COLLATE NOCASE LIMIT ?"
        params.append(limit)
        return [Competition.from_row(r) for r in await self.db.fetchall(sql, params)]

    async def upsert(
        self,
        guild_id: int,
        *,
        source: str,
        external_id: str,
        name: str,
        slug: str | None = None,
        image_url: str | None = None,
        followed: bool = True,
    ) -> Competition:
        await self.db.execute(
            """INSERT INTO competitions (guild_id, source, external_id, name, slug, image_url, followed)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(guild_id, source, external_id) DO UPDATE SET
                   name = excluded.name,
                   slug = COALESCE(excluded.slug, competitions.slug),
                   image_url = COALESCE(excluded.image_url, competitions.image_url),
                   followed = excluded.followed""",
            (guild_id, source, external_id, name, slug, image_url, int(followed)),
        )
        return await self.get_by_external(guild_id, source, external_id)  # type: ignore[return-value]

    async def set_followed(self, competition_id: int, followed: bool) -> None:
        await self.db.execute(
            "UPDATE competitions SET followed = ? WHERE id = ?", (int(followed), competition_id)
        )

    async def create_manual(self, guild_id: int, name: str) -> Competition:
        """Compétition saisie à la main (matchs ajoutés par les organisateurs)."""
        return await self.upsert(
            guild_id, source=SOURCE_MANUAL, external_id=slugify(name), name=name.strip(),
            slug=slugify(name), followed=True,
        )
