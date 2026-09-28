"""Tâche de fond : rappels publics avant le début des événements.

Pour chaque événement programmé et chaque délai configuré (``/admin`` →
``reminder_offsets``, en minutes, ex. ``[1440, 60, 15]``), un rappel est posté dans le salon
de l'annonce, en mentionnant les inscrits (pas la liste d'attente).

Règles (voir ``due_reminders``, testée unitairement) :

- un rappel est « dû » quand ``maintenant >= début - délai`` et que l'événement n'a pas commencé ;
- au plus **un** message par événement et par passage : celui du délai le plus proche du
  début. Les autres rappels dus (en retard, ex. bot redémarré) sont marqués comme traités
  sans être envoyés — on ne spamme pas le salon avec trois rappels d'un coup ;
- un rappel dont l'heure précède la création de l'événement n'est jamais envoyé
  (inutile de rappeler un événement qu'on vient d'annoncer) ;
- le rappel est enregistré (``INSERT OR IGNORE``) **avant** l'envoi : jamais de doublon.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Iterable, Protocol

import discord
from discord.ext import commands, tasks

from bot.features.events.announcement import (
    build_event_view,
    capacity_label,
    event_message_url,
    get_announcement_channel,
)
from bot.features.events.kinds import get_kind
from bot.repositories.events import STATUS_SCHEDULED, Event, EventRepository
from bot.repositories.participants import REGISTERED, WAITLIST, ParticipantRepository
from bot.repositories.reminders import ReminderRepository
from bot.utils.embeds import Colors
from bot.utils.time import discord_full, humanize_minutes, now_utc

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

MAX_MENTIONS_CHARS = 1800


class _HasStart(Protocol):
    starts_at: datetime


@dataclass(slots=True)
class DueReminders:
    send: int | None = None                          # délai (minutes) du rappel à envoyer
    skip: list[int] = field(default_factory=list)    # délais à marquer traités sans envoi

    def __bool__(self) -> bool:
        return self.send is not None or bool(self.skip)


def due_reminders(
    event: _HasStart, offsets: Iterable[int], sent: set[int] | frozenset[int], now: datetime
) -> DueReminders:
    """Calcule les rappels à traiter maintenant (fonction pure, sans base ni Discord)."""
    start = event.starts_at
    if now >= start:
        return DueReminders()
    due = sorted(
        o for o in set(offsets) if o > 0 and o not in sent and now >= start - timedelta(minutes=o)
    )
    if not due:
        return DueReminders()
    created_at: datetime | None = getattr(event, "created_at", None)
    sendable = [o for o in due if created_at is None or start - timedelta(minutes=o) >= created_at]
    if not sendable:
        return DueReminders(send=None, skip=due)
    send = sendable[0]  # le plus proche du début
    return DueReminders(send=send, skip=[o for o in due if o != send])


def remaining_text(start: datetime, now: datetime, offset: int | None = None) -> str:
    """« 15 minutes », « 1 h 20 », « 2 jours »… (utilise le délai exact s'il tombe juste)."""
    minutes = max(1, round((start - now).total_seconds() / 60))
    if offset is not None and abs(minutes - offset) <= 1:
        return humanize_minutes(offset)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes > 1 else ''}"
    if minutes < 1440:
        h, m = divmod(minutes, 60)
        return f"{h} heure{'s' if h > 1 else ''}" if m == 0 else f"{h} h {m:02d}"
    d, rest = divmod(minutes, 1440)
    h = rest // 60
    days = f"{d} jour{'s' if d > 1 else ''}"
    return days if h == 0 else f"{days} et {h} h"


def mentions_block(user_ids: list[int], limit: int = MAX_MENTIONS_CHARS) -> str:
    out: list[str] = []
    used = 0
    for i, uid in enumerate(user_ids):
        m = f"<@{uid}>"
        if used + len(m) + 1 > limit:
            out.append(f"(+{len(user_ids) - i})")
            break
        out.append(m)
        used += len(m) + 1
    return " ".join(out)


async def send_reminder(bot: "STFBot", event: Event, offset: int, now: datetime) -> bool:
    """Poste le rappel public. Renvoie ``False`` si le salon est introuvable ou inaccessible."""
    channel = await get_announcement_channel(bot, event)
    if channel is None:
        log.info("Rappel %s min de l'événement %s ignoré : pas de salon d'annonce", offset, event.id)
        return False
    participants = await ParticipantRepository(bot.db).list(event.id)
    registered = [p.discord_id for p in participants if p.status == REGISTERED]
    waiting = sum(1 for p in participants if p.status == WAITLIST)
    kind = get_kind(event.type)

    when = remaining_text(event.starts_at, now, offset)
    embed = discord.Embed(
        title=f"⏰ {kind.emoji} {event.title} commence dans {when} !"[:256],
        description=f"📅 {discord_full(event.starts_at)}",
        color=Colors.WARNING,
    )
    embed.add_field(name="👥 Inscrits", value=capacity_label(event, len(registered)), inline=True)
    if waiting:
        embed.add_field(name="⏳ Liste d'attente", value=str(waiting), inline=True)
    if url := event_message_url(event):
        embed.add_field(name="🔗 Annonce", value=f"[Voir l'annonce]({url})", inline=True)
    embed.set_footer(text="Un empêchement ? Clique sur « Quitter » pour libérer ta place 🙏")

    content = f"🔔 {mentions_block(registered)}" if registered else None
    view = await build_event_view(bot, event)
    try:
        await channel.send(
            content=content,
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
    except discord.HTTPException as exc:
        log.warning("Rappel %s min de l'événement %s non envoyé : %s", offset, event.id, exc)
        return False
    log.info("Rappel %s min envoyé pour l'événement %s (%d mentions)", offset, event.id, len(registered))
    return True


class EventRemindersTask(commands.Cog):
    """Vérifie chaque minute les rappels à poster."""

    def __init__(self, bot: "STFBot") -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.check_reminders.start()

    async def cog_unload(self) -> None:
        self.check_reminders.cancel()

    @tasks.loop(minutes=1)
    async def check_reminders(self) -> None:
        try:
            await self.run_once()
        except Exception:  # noqa: BLE001 - la boucle ne doit jamais mourir
            log.exception("Erreur dans la tâche des rappels d'événements")

    @check_reminders.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    async def run_once(self, now: datetime | None = None) -> None:
        now = now or now_utc()
        reminders = ReminderRepository(self.bot.db)
        for event in await EventRepository(self.bot.db).list_all_active():
            if event.status != STATUS_SCHEDULED or event.starts_at <= now:
                continue
            try:
                settings = await self.bot.settings.get(event.guild_id)
                plan = due_reminders(event, settings.reminder_offsets, await reminders.sent_offsets(event.id), now)
                if not plan:
                    continue
                for offset in plan.skip:
                    await reminders.mark_sent(event.id, offset)
                if plan.send is not None and await reminders.mark_sent(event.id, plan.send):
                    await send_reminder(self.bot, event, plan.send, now)
            except Exception:  # noqa: BLE001 - un événement en erreur ne bloque pas les autres
                log.exception("Rappel impossible pour l'événement %s", event.id)
