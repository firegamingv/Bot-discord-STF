"""Rappels d'événements : sélection des rappels dus (pure), dépôt, tâche, cycle de vie."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bot.db import Database
from bot.features.events import reminders_task
from bot.features.events.lifecycle_task import EventLifecycleTask, lifecycle_transition
from bot.features.events.reminders_task import (
    DueReminders,
    EventRemindersTask,
    due_reminders,
    mentions_block,
    remaining_text,
)
from bot.repositories.events import STATUS_FINISHED, STATUS_ONGOING, STATUS_SCHEDULED, EventRepository
from bot.repositories.reminders import ReminderRepository
from bot.repositories.settings import SettingsRepository
from bot.utils.time import now_utc

START = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)
CREATED = START - timedelta(days=7)
OFFSETS = [1440, 60, 15]


def ev(start=START, created=CREATED):
    return SimpleNamespace(starts_at=start, created_at=created)


def at(minutes_before: float) -> datetime:
    return START - timedelta(minutes=minutes_before)


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


# ---------------------------------------------------------------------- due_reminders (pure)
def test_nothing_due_long_before():
    assert not due_reminders(ev(), OFFSETS, set(), at(2000))


def test_exact_offset_is_due():
    assert due_reminders(ev(), OFFSETS, set(), at(1440)) == DueReminders(send=1440, skip=[])
    assert due_reminders(ev(), OFFSETS, {1440}, at(60)) == DueReminders(send=60, skip=[])


def test_already_sent_is_not_resent():
    assert not due_reminders(ev(), OFFSETS, {1440}, at(1000))
    assert due_reminders(ev(), OFFSETS, {1440}, at(59)).send == 60


def test_after_start_nothing():
    assert not due_reminders(ev(), OFFSETS, set(), START)
    assert not due_reminders(ev(), OFFSETS, set(), START + timedelta(minutes=5))


def test_restart_sends_only_closest_late_reminder():
    # Bot éteint pendant 1 jour : à 30 min du début, 1440 et 60 sont dus (en retard).
    plan = due_reminders(ev(), OFFSETS, set(), at(30))
    assert plan.send == 60
    assert plan.skip == [1440]
    # Puis à 15 min, le rappel 15 part normalement.
    assert due_reminders(ev(), OFFSETS, {1440, 60}, at(15)).send == 15


def test_all_due_at_once_collapses_to_one_message():
    plan = due_reminders(ev(), OFFSETS, set(), at(5))
    assert plan.send == 15
    assert sorted(plan.skip) == [60, 1440]


def test_reminders_before_creation_are_skipped():
    # Événement créé 2 h avant : le rappel « 1 jour » n'a aucun sens.
    created = at(120)
    plan = due_reminders(ev(created=created), OFFSETS, set(), at(119))
    assert plan.send is None and plan.skip == [1440]
    assert due_reminders(ev(created=created), OFFSETS, {1440}, at(60)).send == 60


def test_invalid_offsets_are_ignored():
    assert not due_reminders(ev(), [0, -5], set(), at(1))


def test_remaining_text():
    assert remaining_text(START, at(15), 15) == "15 minutes"
    assert remaining_text(START, at(1440), 1440) == "1 jour"
    assert remaining_text(START, at(30), 60) == "30 minutes"
    assert remaining_text(START, at(80)) == "1 h 20"
    assert remaining_text(START, at(120)) == "2 heures"
    assert remaining_text(START, at(1440 + 180)) == "1 jour et 3 h"


def test_mentions_block_is_bounded():
    text = mentions_block(list(range(10**17, 10**17 + 500)))
    assert len(text) <= 1900
    assert text.endswith(")")


# ---------------------------------------------------------------------- dépôt
async def test_reminder_repository(db):
    event = await EventRepository(db).create(
        guild_id=1, type="generic", title="T", description=None,
        starts_at=now_utc() + timedelta(hours=1), max_participants=None, created_by=1,
    )
    repo = ReminderRepository(db)
    assert await repo.sent_offsets(event.id) == set()
    assert await repo.mark_sent(event.id, 60) is True
    assert await repo.mark_sent(event.id, 60) is False  # jamais de doublon
    assert await repo.mark_sent(event.id, 15) is True
    assert await repo.sent_offsets(event.id) == {15, 60}
    assert await repo.reset(event.id) == 2
    assert await repo.sent_offsets(event.id) == set()


# ---------------------------------------------------------------------- tâches (sans Discord)
class FakeBot:
    def __init__(self, db):
        self.db = db
        self.settings = SettingsRepository(db)


async def test_reminder_task_marks_before_sending(db, monkeypatch):
    now = now_utc()
    repo = EventRepository(db)
    event = await repo.create(
        guild_id=1, type="generic", title="Bientôt", description=None,
        starts_at=now + timedelta(minutes=14), max_participants=None, created_by=1,
    )
    # created_at est « maintenant » : on le recule pour que les rappels aient un sens
    await db.execute("UPDATE events SET created_at = ? WHERE id = ?",
                     ((now - timedelta(days=2)).isoformat(), event.id))

    calls = []

    async def fake_send(bot, event, offset, now):
        # Le rappel doit déjà être enregistré au moment de l'envoi
        assert offset in await ReminderRepository(bot.db).sent_offsets(event.id)
        calls.append(offset)
        return True

    monkeypatch.setattr(reminders_task, "send_reminder", fake_send)
    task = EventRemindersTask(FakeBot(db))
    await task.run_once(now)
    await task.run_once(now + timedelta(seconds=30))
    assert calls == [15]
    assert await ReminderRepository(db).sent_offsets(event.id) == {15, 60, 1440}


def test_lifecycle_transition():
    e = SimpleNamespace(starts_at=START, status=STATUS_SCHEDULED, is_active=True)
    assert lifecycle_transition(e, at(1)) is None
    assert lifecycle_transition(e, START) == STATUS_ONGOING
    assert lifecycle_transition(e, START + timedelta(hours=7)) == STATUS_FINISHED
    e.status = STATUS_ONGOING
    assert lifecycle_transition(e, START + timedelta(hours=1)) is None
    assert lifecycle_transition(e, START + timedelta(hours=6)) == STATUS_FINISHED
    e.is_active = False
    assert lifecycle_transition(e, START + timedelta(hours=10)) is None


async def test_lifecycle_task_updates_status(db, monkeypatch):
    from bot.features.events import lifecycle_task

    async def no_refresh(bot, event_id):
        return None

    monkeypatch.setattr(lifecycle_task, "refresh_event_message", no_refresh)
    repo = EventRepository(db)
    event = await repo.create(
        guild_id=1, type="generic", title="Go", description=None,
        starts_at=now_utc() + timedelta(minutes=1), max_participants=None, created_by=1,
    )
    task = EventLifecycleTask(FakeBot(db))
    await task.run_once(event.starts_at + timedelta(seconds=1))
    updated = await repo.get(event.id)
    assert updated.status == STATUS_ONGOING and not updated.registration_open
    await task.run_once(event.starts_at + timedelta(hours=6, minutes=1))
    assert (await repo.get(event.id)).status == STATUS_FINISHED
