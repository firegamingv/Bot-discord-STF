"""Accès SQLite asynchrone + système de migrations versionnées.

Toutes les dates sont stockées en texte ISO-8601 UTC (voir ``bot.utils.time.to_db``).
Pour faire évoluer le schéma : ajouter une entrée à la fin de ``MIGRATIONS`` (ne jamais
modifier une migration déjà livrée).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from bot.db.migrations import MIGRATIONS

log = logging.getLogger(__name__)


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._conn: aiosqlite.Connection | None = None
        # Une seule connexion partagée : les écritures et transactions sont sérialisées pour
        # qu'un commit d'une autre fonctionnalité ne valide jamais une transaction à moitié.
        self._write_lock = asyncio.Lock()
        self._tx_owner: asyncio.Task | None = None

    # ------------------------------------------------------------------ cycle de vie
    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA busy_timeout = 5000")
        await self.migrate()
        log.info("Base de données prête (%s)", self.path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() n'a pas été appelé.")
        return self._conn

    # ------------------------------------------------------------------ migrations
    async def migrate(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = await self.fetchone("SELECT MAX(version) AS v FROM schema_version")
        current = (row["v"] if row and row["v"] is not None else 0)
        for version, script in enumerate(MIGRATIONS, start=1):
            if version <= current:
                continue
            log.info("Application de la migration %d", version)
            await self.conn.executescript(script)
            await self.conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            await self.conn.commit()

    # ------------------------------------------------------------------ helpers
    def _in_own_transaction(self) -> bool:
        return self._tx_owner is not None and self._tx_owner is asyncio.current_task()

    async def _write(self, sql: str, params: Sequence[Any] | dict = ()) -> aiosqlite.Cursor:
        if self._in_own_transaction():
            # Appelé depuis une transaction de la même tâche : pas de commit, le bloc s'en charge.
            return await self.conn.execute(sql, params)
        async with self._write_lock:
            cur = await self.conn.execute(sql, params)
            await self.conn.commit()
            return cur

    async def execute(self, sql: str, params: Sequence[Any] | dict = ()) -> int:
        """Exécute une requête d'écriture, commit, et renvoie ``lastrowid``."""
        return (await self._write(sql, params)).lastrowid

    async def execute_rowcount(self, sql: str, params: Sequence[Any] | dict = ()) -> int:
        return (await self._write(sql, params)).rowcount

    async def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        if self._in_own_transaction():
            await self.conn.executemany(sql, seq)
            return
        async with self._write_lock:
            await self.conn.executemany(sql, seq)
            await self.conn.commit()

    async def fetchone(self, sql: str, params: Sequence[Any] | dict = ()) -> aiosqlite.Row | None:
        cur = await self.conn.execute(sql, params)
        return await cur.fetchone()

    async def fetchall(self, sql: str, params: Sequence[Any] | dict = ()) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(sql, params)
        return list(await cur.fetchall())

    async def fetchval(self, sql: str, params: Sequence[Any] | dict = ()) -> Any:
        row = await self.fetchone(sql, params)
        return None if row is None else row[0]

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Bloc atomique : commit si tout va bien, rollback sinon.

        Les transactions (et écritures) des autres tâches attendent la fin du bloc. À
        l'intérieur, on peut utiliser ``conn.execute`` ou les helpers de cette classe (ils ne
        commitent pas dans ce cas). Une transaction imbriquée dans la même tâche est fusionnée
        dans la transaction englobante.
        """
        if self._in_own_transaction():
            yield self.conn
            return
        async with self._write_lock:
            self._tx_owner = asyncio.current_task()
            try:
                await self.conn.execute("BEGIN")
                try:
                    yield self.conn
                except BaseException:
                    await self.conn.rollback()
                    raise
                else:
                    await self.conn.commit()
            finally:
                self._tx_owner = None
