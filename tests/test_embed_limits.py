"""Limites Discord des embeds : 6000 caractères au total, 25 champs, 10 embeds par message."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import discord
import pytest

from bot.db import Database
from bot.features.events.announcement import build_event_embed, build_participants_embed
from bot.features.inhouse.kind import InhouseKind
from bot.features.inhouse.roster import build_roster_embed
from bot.features.inhouse.teams_display import MAX_EMBEDS, _fit
from bot.features.events.kinds import register_kind
from bot.repositories.events import EventRepository
from bot.repositories.inhouse import InhouseRepository
from bot.repositories.participants import ParticipantRepository
from bot.repositories.player_roles import PlayerRoleRepository
from bot.repositories.riot_accounts import RiotAccountRepository
from bot.utils.embeds import CONTINUATION, EMBED_TOTAL_LIMIT, fit_embed
from bot.utils.time import now_utc


def _assert_valid(embed: discord.Embed) -> None:
    assert len(embed) <= EMBED_TOTAL_LIMIT
    assert len(embed.fields) <= 25
    assert all(0 < len(f.value) <= 1024 for f in embed.fields)


# ---------------------------------------------------------------------- fit_embed (pur)
def test_fit_embed_leaves_small_embed_untouched():
    embed = discord.Embed(title="t", description="d")
    embed.add_field(name="a", value="b")
    assert fit_embed(embed) is embed
    assert len(embed.fields) == 1 and embed.description == "d"


def test_fit_embed_drops_continuation_fields_first():
    embed = discord.Embed(title="Inscrits", description="x" * 1500)
    embed.add_field(name="Liste", value="a" * 1024)
    for _ in range(5):
        embed.add_field(name=CONTINUATION, value="b" * 1024)
    embed.add_field(name="Liens", value="c" * 300)
    fit_embed(embed)
    _assert_valid(embed)
    assert embed.description == "x" * 1500
    assert embed.fields[0].name == "Liste" and embed.fields[-1].name == "Liens"


def test_fit_embed_truncates_when_no_continuation():
    embed = discord.Embed(title="t", description="x" * 4000)
    for i in range(3):
        embed.add_field(name=f"f{i}", value="y" * 1024)
    fit_embed(embed)
    _assert_valid(embed)
    assert len(embed.fields) == 3


def test_teams_fit_counts_reserved_embeds():
    items = [discord.Embed(description="z" * 1000) for _ in range(8)]
    summary = discord.Embed(description="s" * 1500)
    kept = _fit(items, reserved=(1, len(summary)))
    total = len(summary) + sum(len(e) for e in kept)
    assert total <= EMBED_TOTAL_LIMIT
    assert len(kept) + 1 <= MAX_EMBEDS


# ---------------------------------------------------------------------- inhouse avec beaucoup d'inscrits
@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


async def test_big_inhouse_embeds_fit_discord_limits(db):
    register_kind(InhouseKind())
    bot = SimpleNamespace(db=db, config=SimpleNamespace(riot_platform="euw1", timezone=ZoneInfo("Europe/Paris")))
    event = await EventRepository(db).create(
        guild_id=1, type="inhouse", title="Grand inhouse", description="d" * 2000,
        starts_at=now_utc() + timedelta(days=1), max_participants=150, created_by=42,
    )
    await InhouseRepository(db).create(event.id, "sr")
    participants = ParticipantRepository(db)
    accounts = RiotAccountRepository(db)
    roles = PlayerRoleRepository(db)
    for uid in range(10**17, 10**17 + 170):
        await accounts.link(uid, puuid=f"p{uid}", game_name="UnPseudoTrèsLong", tag_line="EUW99", platform="euw1")
        await roles.set(uid, ["support", "jungle", "mid", "fill"])
        await participants.add(event.id, uid, max_participants=event.max_participants)

    _assert_valid(await build_event_embed(bot, event))  # type: ignore[arg-type]
    _assert_valid(await build_participants_embed(bot, event))  # type: ignore[arg-type]
    _assert_valid(await build_roster_embed(bot, event, "sr"))  # type: ignore[arg-type]
