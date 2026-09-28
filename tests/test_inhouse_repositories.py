from datetime import datetime, timedelta, timezone

import pytest

from bot.db import Database
from bot.repositories.events import EventRepository
from bot.repositories.inhouse import InhouseRepository
from bot.repositories.inhouse_teams import SUBSTITUTES_INDEX, InhouseTeamRepository


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


async def make_event(db) -> int:
    event = await EventRepository(db).create(
        guild_id=1,
        type="inhouse",
        title="Inhouse du vendredi",
        description=None,
        starts_at=datetime.now(timezone.utc) + timedelta(days=1),
        max_participants=10,
        created_by=99,
    )
    return event.id


async def test_session_create_get_update(db):
    event_id = await make_event(db)
    repo = InhouseRepository(db)
    session = await repo.create(event_id, "sr")
    assert session.game_mode == "sr"
    assert session.teams_message_id is None and not session.teams_published

    session = await repo.update_mode(event_id, "aram")
    assert session.game_mode == "aram"
    with pytest.raises(ValueError):
        await repo.update_mode(event_id, "urf")

    await repo.set_teams_message(event_id, 111, 222)
    session = await repo.get(event_id)
    assert (session.teams_channel_id, session.teams_message_id) == (111, 222)
    assert session.teams_published
    await repo.clear_teams_message(event_id)
    assert (await repo.get(event_id)).teams_message_id is None

    await repo.mark_teams_generated(event_id)
    assert (await repo.get(event_id)).teams_generated_at is not None
    assert (await repo.get_many([event_id, 12345])).keys() == {event_id}


async def test_session_deleted_with_event(db):
    event_id = await make_event(db)
    await InhouseRepository(db).create(event_id, "arena")
    await InhouseTeamRepository(db).save_teams(event_id, [[(1, None), (2, None)]])
    await EventRepository(db).delete(event_id)
    assert await InhouseRepository(db).get(event_id) is None
    assert not (await InhouseTeamRepository(db).get_teams(event_id)).exists


async def test_save_and_get_teams(db):
    event_id = await make_event(db)
    await InhouseRepository(db).create(event_id, "sr")
    repo = InhouseTeamRepository(db)
    assert not await repo.has_teams(event_id)

    await repo.save_teams(event_id, [[(1, "top"), (2, "mid")], [(3, "top"), (4, "mid")]], substitutes=[5])
    stored = await repo.get_teams(event_id)
    assert stored.exists and stored.team_count == 2
    assert [(m.discord_id, m.assigned_role) for m in stored.team(0)] == [(1, "top"), (2, "mid")]
    assert stored.substitutes == [5]
    assert stored.find(5).is_substitute
    assert stored.find(42) is None
    assert stored.as_lists() == [[(1, "top"), (2, "mid")], [(3, "top"), (4, "mid")]]
    assert (await InhouseRepository(db).get(event_id)).teams_generated_at is not None

    # remplacement complet
    await repo.save_teams(event_id, [[(7, None)], [(8, None)]])
    stored = await repo.get_teams(event_id)
    assert stored.all_player_ids() == [7, 8]
    assert stored.substitutes == []


async def test_save_teams_is_atomic(db):
    event_id = await make_event(db)
    repo = InhouseTeamRepository(db)
    await repo.save_teams(event_id, [[(1, None)], [(2, None)]])
    with pytest.raises(ValueError):
        await repo.save_teams(event_id, [[(3, None)], [(3, None)]])
    assert (await repo.get_teams(event_id)).all_player_ids() == [1, 2]


async def test_swap_move_remove(db):
    event_id = await make_event(db)
    repo = InhouseTeamRepository(db)
    await repo.save_teams(event_id, [[(1, "top"), (2, "mid")], [(3, "top"), (4, "mid")]], substitutes=[5])

    await repo.swap_players(event_id, 1, 4)
    stored = await repo.get_teams(event_id)
    assert stored.find(1).team_index == 1 and stored.find(1).assigned_role == "mid"
    assert stored.find(4).team_index == 0 and stored.find(4).assigned_role == "top"

    # un remplaçant prend la place d'un titulaire
    await repo.swap_players(event_id, 5, 2)
    stored = await repo.get_teams(event_id)
    assert stored.find(5).team_index == 0 and stored.find(5).assigned_role == "mid"
    assert stored.substitutes == [2]

    with pytest.raises(LookupError):
        await repo.swap_players(event_id, 1, 999)

    await repo.move_player(event_id, 2, 1, "adc")
    assert (await repo.get_member(event_id, 2)).assigned_role == "adc"
    await repo.move_player(event_id, 3, SUBSTITUTES_INDEX, "top")
    member = await repo.get_member(event_id, 3)
    assert member.is_substitute and member.assigned_role is None
    await repo.move_player(event_id, 77, 0, "support")  # nouveau joueur
    assert (await repo.get_member(event_id, 77)).team_index == 0

    removed = await repo.remove_player(event_id, 1)
    assert removed is not None and removed.team_index == 1
    assert await repo.remove_player(event_id, 1) is None

    await repo.clear(event_id)
    assert not await repo.has_teams(event_id)
