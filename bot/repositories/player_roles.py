"""Rôles de jeu choisis par les joueurs (table ``player_roles``)."""

from __future__ import annotations

from bot.db import Database
from bot.repositories.users import UserRepository

# Ordre canonique + libellés affichés
LOL_ROLES: dict[str, str] = {
    "top": "Top",
    "jungle": "Jungle",
    "mid": "Mid",
    "adc": "ADC",
    "support": "Support",
    "fill": "Fill (peu importe)",
}
LOL_ROLE_EMOJIS: dict[str, str] = {
    "top": "🛡️",
    "jungle": "🌲",
    "mid": "🔮",
    "adc": "🏹",
    "support": "💖",
    "fill": "🎲",
}


def role_label(role: str, *, emoji: bool = True) -> str:
    label = LOL_ROLES.get(role, role)
    return f"{LOL_ROLE_EMOJIS.get(role, '')} {label}".strip() if emoji else label


class PlayerRoleRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, discord_id: int, game: str = "lol") -> list[str]:
        """Rôles triés par priorité (le premier est le rôle principal)."""
        rows = await self.db.fetchall(
            "SELECT role FROM player_roles WHERE discord_id = ? AND game = ? ORDER BY priority ASC",
            (discord_id, game),
        )
        return [r["role"] for r in rows]

    async def get_many(self, discord_ids: list[int], game: str = "lol") -> dict[int, list[str]]:
        if not discord_ids:
            return {}
        placeholders = ",".join("?" * len(discord_ids))
        rows = await self.db.fetchall(
            f"SELECT discord_id, role FROM player_roles WHERE game = ? AND discord_id IN ({placeholders}) "
            "ORDER BY discord_id, priority ASC",
            (game, *discord_ids),
        )
        result: dict[int, list[str]] = {uid: [] for uid in discord_ids}
        for r in rows:
            result[r["discord_id"]].append(r["role"])
        return result

    async def set(self, discord_id: int, roles: list[str], game: str = "lol") -> None:
        """Remplace les rôles du joueur ; l'ordre de la liste = la priorité."""
        unknown = [r for r in roles if r not in LOL_ROLES]
        if unknown:
            raise ValueError(f"Rôles inconnus : {unknown}")
        await UserRepository(self.db).ensure(discord_id)
        async with self.db.transaction() as conn:
            await conn.execute(
                "DELETE FROM player_roles WHERE discord_id = ? AND game = ?", (discord_id, game)
            )
            seen: list[str] = []
            for r in roles:
                if r not in seen:
                    seen.append(r)
            await conn.executemany(
                "INSERT INTO player_roles (discord_id, game, role, priority) VALUES (?, ?, ?, ?)",
                [(discord_id, game, r, i) for i, r in enumerate(seen, start=1)],
            )
