from datetime import datetime, timedelta, timezone

import pytest

from bot.db import Database
from bot.db.migrations import MIGRATIONS
from bot.repositories.events import EventRepository
from bot.repositories.participants import REGISTERED, WAITLIST, ParticipantRepository
from bot.repositories.player_roles import PlayerRoleRepository
from bot.repositories.riot_accounts import RiotAccountRepository
from bot.repositories.settings import SettingsRepository


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


async def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "m.db"
    for _ in range(2):
        database = Database(path)
        await database.connect()
        assert await database.fetchval("SELECT MAX(version) FROM schema_version") == len(MIGRATIONS)
        await database.close()


async def test_settings_defaults_and_update(db):
    repo = SettingsRepository(db)
    s = await repo.get(42)
    assert s.reminder_offsets == [1440, 60, 15]
    s = await repo.update(42, reminder_offsets=[5, 60, 60], announce_channel_id=123)
    assert s.reminder_offsets == [60, 5]
    assert s.announce_channel_id == 123
    with pytest.raises(ValueError):
        await repo.update(42, unknown_setting=1)


async def test_event_crud_and_waitlist(db):
    events = EventRepository(db)
    parts = ParticipantRepository(db)
    start = datetime.now(timezone.utc) + timedelta(days=1)
    e = await events.create(
        guild_id=1, type="generic", title="Soirée", description=None,
        starts_at=start, max_participants=2, created_by=9,
    )
    assert e.is_active and not e.has_started

    for uid in (1, 2, 3, 4):
        await parts.add(e.id, uid, max_participants=2)
    assert [p.discord_id for p in await parts.list(e.id, status=REGISTERED)] == [1, 2]
    assert [p.discord_id for p in await parts.list(e.id, status=WAITLIST)] == [3, 4]

    # Inscription en double : renvoie l'existant
    assert (await parts.add(e.id, 1, max_participants=2)).status == REGISTERED

    await parts.remove(e.id, 1)
    assert await parts.promote_waitlist(e.id, 2) == [3]

    # Réduction de capacité => rétrogradation des derniers inscrits
    promoted, demoted = await parts.rebalance(e.id, 1)
    assert promoted == [] and demoted == [3]

    e = await events.update(e.id, title="Soirée LoL", registration_open=False)
    assert e.title == "Soirée LoL" and not e.registration_open
    assert [x.id for x in await events.search(1, "lol")] == [e.id]

    await events.delete(e.id)
    assert await parts.count(e.id) == 0  # cascade


async def test_roles_and_riot_accounts(db):
    roles = PlayerRoleRepository(db)
    await roles.set(7, ["mid", "top", "mid"])
    assert await roles.get(7) == ["mid", "top"]
    with pytest.raises(ValueError):
        await roles.set(7, ["carry"])

    accounts = RiotAccountRepository(db)
    acc = await accounts.link(7, puuid="p1", game_name="Faker", tag_line="KR1", platform="kr")
    assert acc.riot_id == "Faker#KR1" and acc.verified and acc.rank_score is None
    await accounts.update_rank(7, tier="GOLD", division="II", league_points=50)
    acc = await accounts.get(7)
    assert acc.rank_label == "Or II (50 LP)"
    assert acc.rank_score == 3 * 400 + 2 * 100 + 50
    assert (await accounts.get_by_puuid("p1")).discord_id == 7
    assert await accounts.unlink(7)
