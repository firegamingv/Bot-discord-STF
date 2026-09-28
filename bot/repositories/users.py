"""Utilisateurs Discord connus du bot (table ``users``)."""

from __future__ import annotations

from bot.db import Database


class UserRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def ensure(self, discord_id: int, display_name: str | None = None) -> None:
        """Crée l'utilisateur s'il n'existe pas et met à jour son pseudo."""
        await self.db.execute(
            """INSERT INTO users (discord_id, display_name) VALUES (?, ?)
               ON CONFLICT(discord_id) DO UPDATE SET
                   display_name = COALESCE(excluded.display_name, users.display_name)""",
            (discord_id, display_name),
        )
