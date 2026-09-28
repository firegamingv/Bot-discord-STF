"""Journal de points des pronostics (table ``point_transactions``).

Le **solde** d'un membre sur un serveur = somme de ses transactions. Types (``kind``) :

- ``starting`` : capital de départ (une seule fois) ;
- ``daily``    : bonus quotidien (une fois par jour local) ;
- ``stake``    : mise d'un pari (montant négatif) ;
- ``payout``   : gain d'un pari gagné (mise × cote) ;
- ``refund``   : remboursement (match annulé ou pari modifié) ;
- ``admin``    : ajustement manuel par un organisateur.

Les transactions liées aux paris portent ``competition_id``, ``tournament_name``, ``bet_type``
et ``prediction_id`` : c'est ce qui permet des classements segmentés (période, compétition,
tournoi, type de pari) sans jointure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite

from bot.db import Database
from bot.services.periods import PERIOD_ALL, period_bounds
from bot.utils.time import from_db, now_utc, to_db

KIND_STARTING = "starting"
KIND_DAILY = "daily"
KIND_STAKE = "stake"
KIND_PAYOUT = "payout"
KIND_REFUND = "refund"
KIND_ADMIN = "admin"
BET_KINDS = (KIND_STAKE, KIND_PAYOUT, KIND_REFUND)

KIND_LABELS = {
    KIND_STARTING: "🎁 Capital de départ",
    KIND_DAILY: "☀️ Bonus quotidien",
    KIND_STAKE: "🎲 Mise",
    KIND_PAYOUT: "💰 Gain",
    KIND_REFUND: "↩️ Remboursement",
    KIND_ADMIN: "🛠️ Ajustement",
}


@dataclass(frozen=True, slots=True)
class LeaderboardFilter:
    """Critères d'un classement / de statistiques. ``None`` = pas de filtre."""

    period: str = PERIOD_ALL          # day | week | month | all
    competition_id: int | None = None
    tournament_name: str | None = None
    bet_type: str | None = None       # winner | exact_score


@dataclass(slots=True)
class LeaderboardEntry:
    discord_id: int
    net: int      # gains nets : payout + refund − stake
    wins: int     # paris gagnés
    bets: int     # paris placés


@dataclass(slots=True)
class BalanceEntry:
    discord_id: int
    balance: int


@dataclass(slots=True)
class BetTypeStats:
    bets: int = 0
    won: int = 0
    lost: int = 0
    net: int = 0


@dataclass(slots=True)
class UserStats:
    total: int = 0
    pending: int = 0
    won: int = 0
    lost: int = 0
    refunded: int = 0
    staked: int = 0          # mises des paris réglés (gagnés/perdus)
    returned: int = 0        # gains encaissés
    pending_stake: int = 0   # points actuellement engagés
    best_gain: int = 0       # meilleur gain net sur un pari
    streak_status: str | None = None  # "won" | "lost"
    streak: int = 0
    by_type: dict[str, BetTypeStats] = field(default_factory=dict)

    @property
    def net(self) -> int:
        return self.returned - self.staked

    @property
    def settled(self) -> int:
        return self.won + self.lost

    @property
    def win_rate(self) -> float | None:
        return self.won / self.settled if self.settled else None


@dataclass(slots=True)
class Transaction:
    id: int
    amount: int
    kind: str
    prediction_id: int | None
    competition_id: int | None
    tournament_name: str | None
    bet_type: str | None
    created_at: datetime


def _filter_sql(
    flt: LeaderboardFilter, tz: ZoneInfo, now: datetime | None, *, date_col: str, prefix: str = ""
) -> tuple[str, list]:
    """Fragment SQL ``AND …`` correspondant au filtre."""
    sql = ""
    params: list = []
    start, end = period_bounds(flt.period, tz, now)
    if start is not None:
        sql += f" AND {date_col} >= ?"
        params.append(to_db(start))
    if end is not None:
        sql += f" AND {date_col} < ?"
        params.append(to_db(end))
    if flt.competition_id is not None:
        sql += f" AND {prefix}competition_id = ?"
        params.append(flt.competition_id)
    if flt.tournament_name is not None:
        sql += f" AND {prefix}tournament_name = ?"
        params.append(flt.tournament_name)
    if flt.bet_type is not None:
        sql += f" AND {prefix}bet_type = ?"
        params.append(flt.bet_type)
    return sql, params


class PointsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ solde
    async def balance(self, guild_id: int, discord_id: int) -> int:
        return await self.db.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM point_transactions WHERE guild_id = ? AND discord_id = ?",
            (guild_id, discord_id),
        )

    async def has_wallet(self, guild_id: int, discord_id: int) -> bool:
        return bool(await self.db.fetchval(
            "SELECT 1 FROM point_transactions WHERE guild_id = ? AND discord_id = ? AND kind = ? LIMIT 1",
            (guild_id, discord_id, KIND_STARTING),
        ))

    async def grant_starting(self, guild_id: int, discord_id: int, amount: int) -> bool:
        """Crédite le capital de départ s'il n'a jamais été versé. Atomique ; ``True`` si crédité."""
        rowcount = await self.db.execute_rowcount(
            """INSERT INTO point_transactions (guild_id, discord_id, amount, kind, created_at)
               SELECT ?, ?, ?, ?, ?
               WHERE NOT EXISTS (SELECT 1 FROM point_transactions
                                 WHERE guild_id = ? AND discord_id = ? AND kind = ?)""",
            (guild_id, discord_id, amount, KIND_STARTING, to_db(now_utc()),
             guild_id, discord_id, KIND_STARTING),
        )
        return rowcount > 0

    async def has_daily(self, guild_id: int, discord_id: int, day_start: datetime, day_end: datetime) -> bool:
        return bool(await self.db.fetchval(
            """SELECT 1 FROM point_transactions WHERE guild_id = ? AND discord_id = ? AND kind = ?
               AND created_at >= ? AND created_at < ? LIMIT 1""",
            (guild_id, discord_id, KIND_DAILY, to_db(day_start), to_db(day_end)),
        ))

    async def grant_daily(
        self, guild_id: int, discord_id: int, amount: int, *, day_start: datetime, day_end: datetime,
        now: datetime | None = None,
    ) -> bool:
        """Crédite le bonus du jour s'il n'a pas déjà été versé dans ``[day_start, day_end[``.

        Une seule requête ``INSERT … WHERE NOT EXISTS`` : impossible d'être crédité deux fois,
        même en cliquant très vite.
        """
        rowcount = await self.db.execute_rowcount(
            """INSERT INTO point_transactions (guild_id, discord_id, amount, kind, created_at)
               SELECT ?, ?, ?, ?, ?
               WHERE NOT EXISTS (SELECT 1 FROM point_transactions
                                 WHERE guild_id = ? AND discord_id = ? AND kind = ?
                                   AND created_at >= ? AND created_at < ?)""",
            (guild_id, discord_id, amount, KIND_DAILY, to_db(now or now_utc()),
             guild_id, discord_id, KIND_DAILY, to_db(day_start), to_db(day_end)),
        )
        return rowcount > 0

    async def add(
        self,
        guild_id: int,
        discord_id: int,
        amount: int,
        kind: str,
        *,
        prediction_id: int | None = None,
        competition_id: int | None = None,
        tournament_name: str | None = None,
        bet_type: str | None = None,
    ) -> int:
        """Ajoute une transaction (hors transaction SQL englobante)."""
        return await self.db.execute(
            """INSERT INTO point_transactions (guild_id, discord_id, amount, kind, prediction_id,
                   competition_id, tournament_name, bet_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, discord_id, amount, kind, prediction_id, competition_id, tournament_name,
             bet_type, to_db(now_utc())),
        )

    @staticmethod
    async def add_tx(
        conn: aiosqlite.Connection,
        *,
        guild_id: int,
        discord_id: int,
        amount: int,
        kind: str,
        prediction_id: int | None = None,
        competition_id: int | None = None,
        tournament_name: str | None = None,
        bet_type: str | None = None,
    ) -> None:
        """Même chose, à l'intérieur d'un ``db.transaction()``."""
        await conn.execute(
            """INSERT INTO point_transactions (guild_id, discord_id, amount, kind, prediction_id,
                   competition_id, tournament_name, bet_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, discord_id, amount, kind, prediction_id, competition_id, tournament_name,
             bet_type, to_db(now_utc())),
        )

    async def recent_transactions(self, guild_id: int, discord_id: int, *, limit: int = 10) -> list[Transaction]:
        rows = await self.db.fetchall(
            """SELECT * FROM point_transactions WHERE guild_id = ? AND discord_id = ?
               ORDER BY created_at DESC, id DESC LIMIT ?""",
            (guild_id, discord_id, limit),
        )
        return [
            Transaction(
                id=r["id"], amount=r["amount"], kind=r["kind"], prediction_id=r["prediction_id"],
                competition_id=r["competition_id"], tournament_name=r["tournament_name"],
                bet_type=r["bet_type"], created_at=from_db(r["created_at"]),  # type: ignore[arg-type]
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ classements
    async def leaderboard(
        self,
        guild_id: int,
        flt: LeaderboardFilter,
        tz: ZoneInfo,
        *,
        now: datetime | None = None,
        limit: int | None = 10,
    ) -> list[LeaderboardEntry]:
        """Classement par gains nets (payout + refund − stake) sur le filtre, départage aux
        paris gagnés puis au plus petit nombre de paris."""
        where, params = _filter_sql(flt, tz, now, date_col="created_at")
        sql = f"""
            SELECT discord_id,
                   SUM(amount) AS net,
                   COUNT(DISTINCT CASE WHEN kind = 'payout' THEN prediction_id END) AS wins,
                   COUNT(DISTINCT CASE WHEN kind = 'stake' THEN prediction_id END) AS bets
            FROM point_transactions
            WHERE guild_id = ? AND kind IN ('stake', 'payout', 'refund') {where}
            GROUP BY discord_id
            HAVING bets > 0
            ORDER BY net DESC, wins DESC, bets ASC, discord_id ASC"""
        all_params: list = [guild_id, *params]
        if limit is not None:
            sql += " LIMIT ?"
            all_params.append(limit)
        rows = await self.db.fetchall(sql, all_params)
        return [LeaderboardEntry(r["discord_id"], r["net"], r["wins"], r["bets"]) for r in rows]

    async def leaderboard_rank(
        self, guild_id: int, discord_id: int, flt: LeaderboardFilter, tz: ZoneInfo, *, now: datetime | None = None
    ) -> tuple[int, int, LeaderboardEntry] | None:
        """``(rang, nombre de classés, entrée)`` du membre, ou ``None`` s'il n'est pas classé."""
        entries = await self.leaderboard(guild_id, flt, tz, now=now, limit=None)
        for idx, entry in enumerate(entries, start=1):
            if entry.discord_id == discord_id:
                return idx, len(entries), entry
        return None

    async def balance_leaderboard(self, guild_id: int, *, limit: int | None = 10) -> list[BalanceEntry]:
        """Classement général : solde actuel."""
        sql = """SELECT discord_id, SUM(amount) AS balance FROM point_transactions
                 WHERE guild_id = ? GROUP BY discord_id ORDER BY balance DESC, discord_id ASC"""
        params: list = [guild_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [BalanceEntry(r["discord_id"], r["balance"]) for r in await self.db.fetchall(sql, params)]

    async def balance_rank(self, guild_id: int, discord_id: int) -> tuple[int, int] | None:
        """``(rang, nombre de membres)`` au classement général."""
        entries = await self.balance_leaderboard(guild_id, limit=None)
        for idx, entry in enumerate(entries, start=1):
            if entry.discord_id == discord_id:
                return idx, len(entries)
        return None

    # ------------------------------------------------------------------ statistiques
    async def user_stats(
        self,
        guild_id: int,
        discord_id: int,
        flt: LeaderboardFilter,
        tz: ZoneInfo,
        *,
        now: datetime | None = None,
    ) -> UserStats:
        """Statistiques des paris d'un membre (période appliquée à la date de règlement,
        ou de pari s'il est en cours)."""
        where, params = _filter_sql(
            flt, tz, now, date_col="COALESCE(p.settled_at, p.created_at)", prefix="m."
        )
        where = where.replace("m.bet_type", "p.bet_type")
        rows = await self.db.fetchall(
            f"""SELECT p.bet_type, p.status, p.stake, p.payout,
                       COALESCE(p.settled_at, p.created_at) AS ts
                FROM predictions p JOIN matches m ON m.id = p.match_id
                WHERE p.guild_id = ? AND p.discord_id = ? {where}
                ORDER BY ts DESC, p.id DESC""",
            (guild_id, discord_id, *params),
        )
        stats = UserStats()
        streak_open = True
        for r in rows:
            status, stake, payout = r["status"], r["stake"], r["payout"]
            per_type = stats.by_type.setdefault(r["bet_type"], BetTypeStats())
            stats.total += 1
            per_type.bets += 1
            if status == "pending":
                stats.pending += 1
                stats.pending_stake += stake
                continue
            if status == "refunded":
                stats.refunded += 1
                continue
            stats.staked += stake
            stats.returned += payout
            per_type.net += payout - stake
            if status == "won":
                stats.won += 1
                per_type.won += 1
                stats.best_gain = max(stats.best_gain, payout - stake)
            else:
                stats.lost += 1
                per_type.lost += 1
            # Série en cours : paris réglés les plus récents de même issue
            if streak_open:
                if stats.streak_status is None:
                    stats.streak_status, stats.streak = status, 1
                elif stats.streak_status == status:
                    stats.streak += 1
                else:
                    streak_open = False
        return stats
