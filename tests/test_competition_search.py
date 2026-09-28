from bot.features.predictions.competition_follow import search_leagues
from bot.services.lolesports_api import LeagueDTO


def lg(i, slug, name, region, priority=100):
    return LeagueDTO(id=str(i), slug=slug, name=name, region=region, image=None, priority=priority)


LEAGUES = [
    lg(1, "lec", "LEC", "EMEA", 1),
    lg(2, "lfl", "LFL", "France", 50),
    lg(3, "emea_masters", "EMEA Masters", "EMEA", 30),
    lg(4, "lck", "LCK", "Corée", 2),
    lg(5, "nlc", "NLC", "Europe du Nord", 60),
]


def names(query):
    return [x.name for x in search_leagues(LEAGUES, query)]


def test_exact_and_prefix_first():
    assert names("lfl") == ["LFL"]
    assert names("LEC")[0] == "LEC"


def test_accents_case_and_region():
    assert "LFL" in names("france")
    assert names("corée") == ["LCK"]
    assert names("Masters") == ["EMEA Masters"]


def test_empty_query_sorted_by_priority():
    assert names("")[:2] == ["LEC", "LCK"]


def test_no_match():
    assert names("zzz") == []
