from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from bot.core.errors import UserFacingError
from bot.db import Database
from bot.features.predictions.betting_service import place_bet
from bot.features.predictions.settlement_service import cancel_match, settle_match
from bot.features.predictions.wallet_service import ensure_wallet
from bot.repositories.competitions import CompetitionRepository
from bot.repositories.matches import STATE_COMPLETED, MatchRepository
from bot.repositories.points import LeaderboardFilter, PointsRepository
from bot.repositories.predictions import PredictionRepository
from bot.repositories.settings import SettingsRepository
from bot.services.periods import day_bounds

PARIS = ZoneInfo("Europe/Paris")
UTC = timezone.utc
GUILD = 1


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
def bot(db):
    return SimpleNamespace(db=db, settings=SettingsRepository(db), config=SimpleNamespace(timezone=PARIS))


def user(uid: int):
    return SimpleNamespace(id=uid, display_name=f"membre{uid}")


async def make_match(db, comp_name="LEC", hours=2, best_of=3, tournament="LEC Summer 2026"):
    comp = await CompetitionRepository(db).upsert(
        GUILD, source="lolesports", external_id=comp_name.lower(), name=comp_name, slug=comp_name.lower()
    )
    match = await MatchRepository(db).create_manual(
        GUILD, comp.id, team1_name="G2 Esports", team2_name="Fnatic", team1_code="G2", team2_code="FNC",
        starts_at=datetime.now(UTC) + timedelta(hours=hours), best_of=best_of, tournament_name=tournament,
    )
    return comp, match


async def test_daily_bonus_only_once(db):
    points = PointsRepository(db)
    start, end = day_bounds(PARIS)
    assert await points.grant_starting(GUILD, 10, 500)
    assert not await points.grant_starting(GUILD, 10, 500)
    assert await points.grant_daily(GUILD, 10, 100, day_start=start, day_end=end)
    assert not await points.grant_daily(GUILD, 10, 100, day_start=start, day_end=end)
    assert await points.has_daily(GUILD, 10, start, end)
    # Le lendemain : nouveau bonus
    s2, e2 = day_bounds(PARIS, offset_days=1)
    assert await points.grant_daily(GUILD, 10, 100, day_start=s2, day_end=e2, now=s2 + timedelta(hours=1))
    assert await points.balance(GUILD, 10) == 700
    # Autre serveur : portefeuille indépendant
    assert await points.balance(2, 10) == 0


async def test_ensure_wallet_messages(bot):
    first = await ensure_wallet(bot, GUILD, user(5))
    assert first.got_starting and first.got_daily
    assert first.balance == 600 and "bonus du jour" in first.notice
    second = await ensure_wallet(bot, GUILD, user(5))
    assert second.notice is None and second.balance == 600


async def test_bet_modify_settle_and_leaderboard(bot, db):
    comp, match = await make_match(db)
    other_comp, other_match = await make_match(db, comp_name="LCK", tournament="LCK Summer 2026")

    r1 = await place_bet(bot, GUILD, user(1), match.id, bet_type="winner", choice="1", stake=100)
    assert r1.balance == 500 and r1.notice
    # Modification : ancienne mise remboursée, nouvelle prélevée
    r1b = await place_bet(bot, GUILD, user(1), match.id, bet_type="winner", choice="1", stake=200)
    assert r1b.previous_stake == 100 and r1b.balance == 400
    await place_bet(bot, GUILD, user(1), match.id, bet_type="exact_score", choice="2-1", stake=50)
    await place_bet(bot, GUILD, user(2), match.id, bet_type="winner", choice="2", stake=300)
    await place_bet(bot, GUILD, user(3), other_match.id, bet_type="winner", choice="1", stake=100)

    with pytest.raises(UserFacingError):
        await place_bet(bot, GUILD, user(2), match.id, bet_type="winner", choice="2", stake=10_000)
    with pytest.raises(UserFacingError):
        await place_bet(bot, GUILD, user(2), match.id, bet_type="winner", choice="2", stake=5)

    await MatchRepository(db).set_result(match.id, team1_score=2, team2_score=1, winner=1, state=STATE_COMPLETED)
    report = await settle_match(bot, match.id, notify=False)
    assert report is not None
    assert report.winners == [(1, 200 + 125)]  # 200×2−200 + round(50×3.5)−50
    assert report.losers == 1
    # Idempotent
    assert await settle_match(bot, match.id, notify=False) is None

    points = PointsRepository(db)
    assert await points.balance(GUILD, 1) == 600 - 250 + 400 + 175
    assert await points.balance(GUILD, 2) == 300

    board = await points.leaderboard(GUILD, LeaderboardFilter(), PARIS)
    assert [(e.discord_id, e.net, e.wins, e.bets) for e in board][:1] == [(1, 325, 2, 2)]
    assert board[-1].discord_id == 2 and board[-1].net == -300

    # Filtre par compétition : le pari en attente sur la LCK n'apparaît que là
    lec = await points.leaderboard(GUILD, LeaderboardFilter(competition_id=comp.id), PARIS)
    assert {e.discord_id for e in lec} == {1, 2}
    lck = await points.leaderboard(GUILD, LeaderboardFilter(competition_id=other_comp.id), PARIS)
    assert [(e.discord_id, e.net) for e in lck] == [(3, -100)]
    # Filtre par type de pari et par tournoi
    exact = await points.leaderboard(GUILD, LeaderboardFilter(bet_type="exact_score"), PARIS)
    assert [(e.discord_id, e.net) for e in exact] == [(1, 125)]
    tourn = await points.leaderboard(GUILD, LeaderboardFilter(tournament_name="LCK Summer 2026"), PARIS)
    assert [e.discord_id for e in tourn] == [3]
    # Filtre par période : tout s'est passé aujourd'hui ; rien le mois dernier
    today = await points.leaderboard(GUILD, LeaderboardFilter(period="day"), PARIS)
    assert len(today) == 3
    past = await points.leaderboard(
        GUILD, LeaderboardFilter(period="month"), PARIS, now=datetime.now(UTC) - timedelta(days=40)
    )
    assert past == []

    rank = await points.leaderboard_rank(GUILD, 2, LeaderboardFilter(), PARIS)
    assert rank is not None and rank[0] == 3
    assert (await points.balance_rank(GUILD, 1))[0] == 1

    stats = await points.user_stats(GUILD, 1, LeaderboardFilter(), PARIS)
    assert (stats.total, stats.won, stats.lost, stats.net) == (2, 2, 0, 325)
    assert stats.streak_status == "won" and stats.streak == 2
    assert stats.best_gain == 200
    assert stats.by_type["exact_score"].net == 125


async def test_cancel_refunds(bot, db):
    _, match = await make_match(db)
    await place_bet(bot, GUILD, user(7), match.id, bet_type="winner", choice="2", stake=100)
    report = await cancel_match(bot, match.id, notify=False)
    assert report is not None and report.refunded == 1 and report.cancelled
    assert await PointsRepository(db).balance(GUILD, 7) == 600
    pred = await PredictionRepository(db).get_user_bet(match.id, 7, "winner")
    assert pred.status == "refunded"


async def test_bets_closed_after_start(bot, db):
    _, match = await make_match(db, hours=-1)
    with pytest.raises(UserFacingError):
        await place_bet(bot, GUILD, user(8), match.id, bet_type="winner", choice="1", stake=50)


async def test_exact_score_refused_for_bo1(bot, db):
    _, match = await make_match(db, best_of=1)
    with pytest.raises(UserFacingError):
        await place_bet(bot, GUILD, user(9), match.id, bet_type="exact_score", choice="1-0", stake=50)
