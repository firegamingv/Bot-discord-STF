from datetime import datetime, timezone

from bot.services.lolesports_api import (
    parse_leagues,
    parse_schedule,
    parse_tournaments,
    pretty_tournament_name,
)

SCHEDULE = {
    "data": {
        "schedule": {
            "pages": {"older": "b2xkZXI6OjExMDg1", "newer": "bmV3ZXI6OjExMDg1"},
            "events": [
                {
                    "startTime": "2026-09-28T16:00:00Z",
                    "state": "completed",
                    "type": "match",
                    "blockName": "Semaine 3",
                    "league": {"name": "LEC", "slug": "lec"},
                    "match": {
                        "id": "113487400974342837",
                        "flags": ["hasVod"],
                        "teams": [
                            {"name": "G2 Esports", "code": "G2", "image": "http://x/g2.png",
                             "result": {"outcome": "win", "gameWins": 2},
                             "record": {"wins": 5, "losses": 1}},
                            {"name": "Fnatic", "code": "FNC", "image": "http://x/fnc.png",
                             "result": {"outcome": "loss", "gameWins": 1},
                             "record": {"wins": 3, "losses": 3}},
                        ],
                        "strategy": {"type": "bestOf", "count": 3},
                    },
                },
                {
                    "startTime": "2026-09-29T18:00:00Z",
                    "state": "unstarted",
                    "type": "match",
                    "blockName": "Semaine 3",
                    "league": {"name": "LEC", "slug": "lec"},
                    "match": {
                        "id": "113487400974342838",
                        "flags": [],
                        "teams": [
                            {"name": "Karmine Corp", "code": "KC", "image": "", "result": None,
                             "record": None},
                            {"name": "Team Vitality", "code": "VIT", "image": "", "result": None,
                             "record": None},
                        ],
                        "strategy": {"type": "bestOf", "count": 1},
                    },
                },
                {"startTime": "2026-09-29T15:00:00Z", "state": "unstarted", "type": "show",
                 "blockName": "Pré-show", "league": {"name": "LEC", "slug": "lec"}},
                {"startTime": "pas une date", "state": "unstarted", "type": "match",
                 "league": {"name": "LEC", "slug": "lec"},
                 "match": {"id": "bad", "teams": [{"name": "A"}, {"name": "B"}]}},
                {"type": "match", "startTime": "2026-09-30T18:00:00Z", "match": {"id": "x", "teams": [{}]}},
            ],
        }
    }
}


def test_parse_schedule_realistic():
    sched = parse_schedule(SCHEDULE, league_ids=["98767991302996019"])
    assert sched.older_token == "b2xkZXI6OjExMDg1"
    assert sched.newer_token == "bmV3ZXI6OjExMDg1"
    assert len(sched.events) == 2  # show + 2 malformés ignorés
    done, upcoming = sched.events
    assert done.external_id == "113487400974342837"
    assert done.league_id == "98767991302996019"
    assert done.league_slug == "lec" and done.league_name == "LEC"
    assert done.block_name == "Semaine 3"
    assert (done.team1_name, done.team1_code, done.team2_code) == ("G2 Esports", "G2", "FNC")
    assert done.best_of == 3
    assert done.starts_at == datetime(2026, 9, 28, 16, tzinfo=timezone.utc)
    assert done.state == "completed"
    assert (done.team1_wins, done.team2_wins, done.winner) == (2, 1, 1)
    assert upcoming.state == "unstarted" and upcoming.winner is None
    assert upcoming.team1_wins is None and upcoming.best_of == 1


def test_parse_schedule_multi_league_has_no_league_id():
    sched = parse_schedule(SCHEDULE, league_ids=["1", "2"])
    assert all(e.league_id is None for e in sched.events)


def test_parse_schedule_garbage():
    assert parse_schedule({"oops": 1}).events == []
    assert parse_schedule(None).events == []


def test_parse_leagues():
    payload = {"data": {"leagues": [
        {"id": "98767991302996019", "slug": "lec", "name": "LEC", "region": "EMEA",
         "image": "http://x/lec.png", "priority": 3, "displayPriority": {"position": 2, "status": "selected"}},
        {"id": "98767991310872058", "slug": "lck", "name": "LCK", "region": "CORÉE", "image": "http://x/lck.png",
         "priority": 1},
        {"slug": "sans-id"},
    ]}}
    leagues = parse_leagues(payload)
    assert [lg.slug for lg in leagues] == ["lec", "lck"]
    assert leagues[0].priority == 2 and leagues[1].priority == 1


def test_parse_tournaments_and_names():
    payload = {"data": {"leagues": [{"tournaments": [
        {"id": "1", "slug": "lec_summer_2026", "startDate": "2026-06-14", "endDate": "2026-09-30"},
        {"id": "2", "slug": "worlds_2026", "startDate": "2026-10-01", "endDate": "2026-11-10"},
    ]}]}}
    tournaments = parse_tournaments(payload)
    assert [t.name for t in tournaments] == ["LEC Summer 2026", "Worlds 2026"]
    assert tournaments[0].contains(datetime(2026, 9, 30, 20, tzinfo=timezone.utc))
    assert not tournaments[0].contains(datetime(2026, 10, 1, 1, tzinfo=timezone.utc))
    assert pretty_tournament_name("msi_2026") == "MSI 2026"
