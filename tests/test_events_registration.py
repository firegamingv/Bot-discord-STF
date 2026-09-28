"""Inscriptions aux événements : cœur sans Discord + parcours join/leave avec de faux objets."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from bot.core.errors import NotFoundError, UserFacingError
from bot.db import Database
from bot.features.events import registration_service as rs
from bot.features.events.announcement import capacity_label, generic_event_embed, mention_list
from bot.features.events.autocomplete import event_choice_label
from bot.features.events.kinds import EventKind, register_kind
from bot.repositories.events import STATUS_FINISHED, EventRepository
from bot.repositories.participants import REGISTERED, WAITLIST, ParticipantRepository
from bot.repositories.settings import SettingsRepository
from bot.utils.time import now_utc

GUILD = 1


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


async def make_event(db, *, places=None, hours=2, type="generic", **kw):
    event = await EventRepository(db).create(
        guild_id=GUILD, type=type, title="Soirée test", description=None,
        starts_at=now_utc() + timedelta(hours=hours), max_participants=places, created_by=42,
    )
    if kw:
        event = await EventRepository(db).update(event.id, **kw)
    return event


# ---------------------------------------------------------------------- cœur sans Discord
async def test_register_then_waitlist(db):
    event = await make_event(db, places=2)
    r1 = await rs.register_participant(db, event.id, 1)
    r2 = await rs.register_participant(db, event.id, 2)
    r3 = await rs.register_participant(db, event.id, 3)
    r4 = await rs.register_participant(db, event.id, 4)
    assert not r1.on_waitlist and not r2.on_waitlist
    assert r3.on_waitlist and r3.position == 1
    assert r4.on_waitlist and r4.position == 2
    assert r4.registered_count == 2


async def test_register_twice_is_idempotent(db):
    event = await make_event(db, places=5)
    await rs.register_participant(db, event.id, 1)
    again = await rs.register_participant(db, event.id, 1)
    assert again.already
    assert await ParticipantRepository(db).count(event.id) == 1


async def test_concurrent_joins_never_exceed_capacity(db):
    event = await make_event(db, places=5)
    results = await asyncio.gather(*(rs.register_participant(db, event.id, uid) for uid in range(100, 130)))
    repo = ParticipantRepository(db)
    assert await repo.count(event.id) == 5
    assert await repo.count(event.id, status=WAITLIST) == 25
    positions = sorted(r.position for r in results if r.on_waitlist)
    assert positions == list(range(1, 26))


async def test_unregister_promotes_first_in_waitlist(db):
    event = await make_event(db, places=1)
    await rs.register_participant(db, event.id, 1)
    await rs.register_participant(db, event.id, 2)
    await rs.register_participant(db, event.id, 3)
    result = await rs.unregister_participant(db, event.id, 1)
    assert result.removed is not None and result.removed.status == REGISTERED
    assert result.promoted == [2]
    assert (await ParticipantRepository(db).get(event.id, 2)).status == REGISTERED
    assert await rs.waitlist_position(db, event.id, 3) == 1


async def test_unregister_from_waitlist_promotes_nobody(db):
    event = await make_event(db, places=1)
    await rs.register_participant(db, event.id, 1)
    await rs.register_participant(db, event.id, 2)
    result = await rs.unregister_participant(db, event.id, 2)
    assert result.removed.on_waitlist
    assert result.promoted == []


async def test_unregister_unknown_member(db):
    event = await make_event(db)
    result = await rs.unregister_participant(db, event.id, 999)
    assert result.removed is None


@pytest.mark.parametrize(
    "kw, needle",
    [
        ({"registration_open": False}, "fermées"),
        ({"status": STATUS_FINISHED}, "terminé"),
    ],
)
async def test_check_joinable_refuses(db, kw, needle):
    event = await make_event(db, **kw)
    with pytest.raises(UserFacingError, match=needle):
        rs.check_joinable(event)


async def test_check_joinable_started_and_missing(db):
    event = await make_event(db, hours=-1)
    with pytest.raises(UserFacingError, match="commencé"):
        rs.check_joinable(event)
    with pytest.raises(NotFoundError):
        rs.check_joinable(None)


async def test_register_refused_when_closed(db):
    event = await make_event(db, registration_open=False)
    with pytest.raises(UserFacingError):
        await rs.register_participant(db, event.id, 1)
    assert await ParticipantRepository(db).count(event.id) == 0


# ---------------------------------------------------------------------- affichage
async def test_labels_and_embed(db):
    event = await make_event(db, places=10)
    assert capacity_label(event, 7) == "7/10"
    assert capacity_label(event, 10) == "10/10 (complet)"
    unlimited = await make_event(db)
    assert capacity_label(unlimited, 3) == "3 · illimité"

    for uid in (1, 2):
        await rs.register_participant(db, event.id, uid)
    embed = generic_event_embed(event, await ParticipantRepository(db).list(event.id))
    assert embed.title.endswith("Soirée test")
    assert f"Événement #{event.id}" in embed.footer.text
    assert any("<@1>" in f.value for f in embed.fields)

    label = event_choice_label(event, ZoneInfo("Europe/Paris"))
    assert label.startswith(f"#{event.id} · ") and len(label) <= 100


def test_mention_list_is_bounded():
    text = mention_list(list(range(10**17, 10**17 + 200)))
    assert len(text) <= 1024
    assert "autres" in text
    assert mention_list([]) == "*Personne pour l'instant.*"


# ---------------------------------------------------------------------- parcours Discord simulé
class FakeResponse:
    def __init__(self, sink):
        self.sink = sink
        self._done = False

    def is_done(self):
        return self._done

    async def send_message(self, **kwargs):
        self._done = True
        self.sink.append(kwargs)

    async def defer(self, **kwargs):
        self._done = True


class FakeFollowup:
    def __init__(self, sink):
        self.sink = sink

    async def send(self, **kwargs):
        self.sink.append(kwargs)


class FakeBot:
    def __init__(self, db):
        self.db = db
        self.settings = SettingsRepository(db)
        self.config = SimpleNamespace(timezone=ZoneInfo("Europe/Paris"))

    def get_channel(self, _id):
        return None

    async def fetch_channel(self, _id):
        raise RuntimeError("pas de Discord dans les tests")

    def get_user(self, _id):
        return None


def fake_interaction(bot, user_id):
    sent: list[dict] = []
    user = SimpleNamespace(id=user_id, display_name=f"user{user_id}", mention=f"<@{user_id}>")
    inter = SimpleNamespace(
        client=bot, user=user, guild_id=GUILD, response=FakeResponse(sent), followup=FakeFollowup(sent)
    )
    return inter, sent


class RecordingKind(EventKind):
    key = "test-kind"
    label = "Test"
    emoji = "🧪"

    def __init__(self):
        self.left: list[int] = []

    async def check_can_join(self, bot, interaction, event):
        if interaction.user.id == 666:
            raise UserFacingError("Lie ton compte d'abord !")

    async def on_joined(self, bot, interaction, event, participant):
        return "Pense à choisir tes rôles."

    async def on_left(self, bot, event, discord_id):
        self.left.append(discord_id)


async def test_join_and_leave_flow_with_kind_hooks(db):
    kind = RecordingKind()
    register_kind(kind)
    bot = FakeBot(db)
    event = await make_event(db, places=1, type=kind.key)

    inter, sent = fake_interaction(bot, 10)
    await rs.join_event(bot, inter, event.id)
    embed = sent[-1]["embed"]
    assert sent[-1]["ephemeral"] is True
    assert "Inscription confirmée" in embed.title
    assert any("choisir tes rôles" in f.value for f in embed.fields)

    inter2, sent2 = fake_interaction(bot, 11)
    await rs.join_event(bot, inter2, event.id)
    assert "complet" in sent2[-1]["embed"].title.lower()
    assert "n°1" in sent2[-1]["embed"].description

    inter3, _ = fake_interaction(bot, 666)
    with pytest.raises(UserFacingError, match="Lie ton compte"):
        await rs.join_event(bot, inter3, event.id)

    # Désinscription : on_left appelé, le n°1 de la liste d'attente est promu
    inter4, sent4 = fake_interaction(bot, 10)
    await rs.leave_event(bot, inter4, event.id)
    assert kind.left == [10]
    assert "désinscrit" in sent4[-1]["embed"].description
    assert (await ParticipantRepository(db).get(event.id, 11)).status == REGISTERED


async def test_join_other_guild_event_is_not_found(db):
    bot = FakeBot(db)
    event = await EventRepository(db).create(
        guild_id=999, type="generic", title="Ailleurs", description=None,
        starts_at=now_utc() + timedelta(hours=1), max_participants=None, created_by=1,
    )
    inter, _ = fake_interaction(bot, 10)
    with pytest.raises(NotFoundError):
        await rs.join_event(bot, inter, event.id)


async def test_leave_when_not_registered_is_friendly(db):
    bot = FakeBot(db)
    event = await make_event(db)
    inter, sent = fake_interaction(bot, 10)
    await rs.leave_event(bot, inter, event.id)
    assert "pas inscrit" in sent[-1]["embed"].description
