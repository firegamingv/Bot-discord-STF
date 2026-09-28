"""Statistiques de participation d'un membre aux événements du serveur (pour ``/compte profil``).

Requête en lecture seule sur ``event_participants`` / ``events``. Candidate à rejoindre
``bot/repositories/participants.py`` (méthode ``stats_for_user``) si d'autres domaines en ont besoin.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.db import Database
from bot.utils.time import now_utc, to_db


@dataclass(slots=True)
class ParticipationStats:
    total: int = 0  # inscriptions confirmées (hors liste d'attente, hors événements annulés)
    inhouses: int = 0  # dont inhouses
    upcoming: int = 0  # événements à venir où le membre est inscrit
    waitlist: int = 0  # événements à venir où il est en liste d'attente


async def get_participation_stats(db: Database, guild_id: int, discord_id: int) -> ParticipationStats:
    row = await db.fetchone(
        """SELECT
               SUM(CASE WHEN p.status = 'registered' THEN 1 ELSE 0 END)                       AS total,
               SUM(CASE WHEN p.status = 'registered' AND e.type = 'inhouse' THEN 1 ELSE 0 END) AS inhouses,
               SUM(CASE WHEN p.status = 'registered' AND e.starts_at > :now
                             AND e.status IN ('scheduled', 'ongoing') THEN 1 ELSE 0 END)       AS upcoming,
               SUM(CASE WHEN p.status = 'waitlist' AND e.starts_at > :now
                             AND e.status IN ('scheduled', 'ongoing') THEN 1 ELSE 0 END)       AS waitlist
           FROM event_participants p
           JOIN events e ON e.id = p.event_id
           WHERE p.discord_id = :uid AND e.guild_id = :gid AND e.status != 'cancelled'""",
        {"uid": discord_id, "gid": guild_id, "now": to_db(now_utc())},
    )
    if row is None:
        return ParticipationStats()
    return ParticipationStats(
        total=row["total"] or 0,
        inhouses=row["inhouses"] or 0,
        upcoming=row["upcoming"] or 0,
        waitlist=row["waitlist"] or 0,
    )
