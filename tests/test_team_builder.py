import random
import time
from collections import Counter

import pytest

from bot.services.team_builder import (
    DEFAULT_RANK_SCORE,
    LANE_ROLES,
    MODE_ARAM,
    MODE_ARENA,
    MODE_SR,
    NotEnoughPlayersError,
    PlayerInfo,
    average_gap,
    build_teams,
    effective_scores,
    match_layout,
    plan_teams,
    role_category,
    role_penalty,
    role_stats,
)


def ids_of(result):
    return sorted(pid for t in result.teams for pid, _ in t.players)


def random_players(n, seed=0, with_roles=True):
    rng = random.Random(seed)
    pool = [*LANE_ROLES, "fill"]
    return [
        PlayerInfo(
            discord_id=1000 + i,
            roles=rng.sample(pool, 2) if with_roles else (),
            rank_score=rng.choice([None, rng.uniform(0, 3000)]),
        )
        for i in range(n)
    ]


# ------------------------------------------------------------------ utilitaires
def test_role_penalty_order():
    roles = ["mid", "top"]
    assert role_penalty(roles, "mid") == 0
    assert role_penalty(roles, "top") == 1
    assert role_penalty(roles, "adc") > role_penalty(["mid", "fill"], "adc") > role_penalty(roles, "top")
    assert role_penalty([], "adc") < role_penalty(roles, "adc")
    assert role_penalty(roles, None) == 0
    assert role_category(["fill"], "adc") == "main"
    assert role_category(["mid", "fill"], "adc") == "fill"
    assert role_category(["mid"], "adc") == "offrole"


def test_unknown_ranks_use_known_average_or_default():
    players = [PlayerInfo(1, (), 1000), PlayerInfo(2, (), 2000), PlayerInfo(3, (), None)]
    assert effective_scores(players)[3] == 1500
    assert effective_scores([PlayerInfo(1), PlayerInfo(2)])[1] == DEFAULT_RANK_SCORE


def test_plan_and_layout():
    assert plan_teams(MODE_SR, 10) == (2, 0)
    assert plan_teams(MODE_SR, 17) == (2, 7)
    assert plan_teams(MODE_SR, 20) == (4, 0)
    assert plan_teams(MODE_ARAM, 15) == (2, 5)  # nombre d'équipes pair
    assert plan_teams(MODE_ARENA, 9) == (4, 1)
    with pytest.raises(NotEnoughPlayersError):
        plan_teams(MODE_SR, 9)
    with pytest.raises(NotEnoughPlayersError):
        plan_teams(MODE_ARENA, 3)
    assert match_layout(MODE_SR, 4) == [0, 0, 1, 1]
    assert match_layout(MODE_ARENA, 8) == [0] * 8
    assert match_layout(MODE_ARENA, 10) == [0] * 5 + [1] * 5
    assert match_layout(MODE_ARENA, 9) == [0] * 5 + [1] * 4


# ------------------------------------------------------------------ Faille
def test_sr_sizes_and_substitutes_are_last_registered():
    players = random_players(13)
    result = build_teams(players, MODE_SR, seed=1)
    assert len(result.teams) == 2
    assert all(t.size == 5 for t in result.teams)
    assert result.substitutes == [p.discord_id for p in players[10:]]
    assert ids_of(result) == sorted(p.discord_id for p in players[:10])
    assert [t.match_index for t in result.teams] == [0, 0]


def test_sr_each_team_has_every_role_once():
    result = build_teams(random_players(20, seed=3), MODE_SR, seed=5)
    assert len(result.teams) == 4
    assert [t.match_index for t in result.teams] == [0, 0, 1, 1]
    for team in result.teams:
        assert [role for _, role in team.players] == list(LANE_ROLES)


def test_sr_ideal_case_everyone_gets_main_role():
    # 2 joueurs par rôle principal, mêmes rangs : 100 % de rôles principaux attendus
    players = []
    for i, role in enumerate(LANE_ROLES * 2):
        secondary = LANE_ROLES[(LANE_ROLES.index(role) + 1) % 5]
        players.append(PlayerInfo(i, [role, secondary], 1500))
    for seed in range(5):
        result = build_teams(players, MODE_SR, seed=seed)
        stats = role_stats(result, players)
        assert stats["main"] == 10, stats


def test_sr_ideal_case_twenty_players():
    rng = random.Random(9)
    players = [
        PlayerInfo(i, [role], rng.uniform(800, 2000)) for i, role in enumerate(LANE_ROLES * 4)
    ]
    result = build_teams(players, MODE_SR, seed=2)
    assert role_stats(result, players)["main"] == 20
    assert result.average_gap < 60


def test_sr_balances_ranks_between_teams():
    # Deux joueurs par rôle : un fort et un faible. Il faut les répartir un de chaque côté.
    players = []
    for i, role in enumerate(LANE_ROLES):
        strong, weak = (2500, 500) if role != "support" else (1500, 1500)
        players.append(PlayerInfo(2 * i, [role], strong))
        players.append(PlayerInfo(2 * i + 1, [role], weak))
    result = build_teams(players, MODE_SR, seed=4)
    assert role_stats(result, players)["main"] == 10
    assert result.teams[0].total_score == result.teams[1].total_score
    assert average_gap(result.teams) == 0


def test_sr_prefers_roles_but_fixes_huge_rank_gaps():
    # Tous les forts veulent le même camp de rôles : sans équilibrage, écart énorme
    players = random_players(10, seed=11)
    result = build_teams(players, MODE_SR, seed=11)
    scores = result.effective_scores
    worst_split = abs(
        sum(sorted(scores.values())[5:]) - sum(sorted(scores.values())[:5])
    ) / 5
    assert result.average_gap < worst_split / 3


# ------------------------------------------------------------------ ARAM
def test_aram_no_roles_and_balanced():
    players = [PlayerInfo(i, ("mid",), float(s)) for i, s in enumerate([0, 400, 800, 1200, 1600, 2000, 2400, 2800, 1000, 1800])]
    result = build_teams(players, MODE_ARAM, seed=7)
    assert len(result.teams) == 2
    assert all(role is None for t in result.teams for _, role in t.players)
    assert result.average_gap <= 40  # 200 points d'écart total au pire / 5 joueurs


def test_aram_random_players_well_balanced():
    players = random_players(20, seed=21, with_roles=False)
    result = build_teams(players, MODE_ARAM, seed=21)
    assert len(result.teams) == 4
    assert result.average_gap < 50


# ------------------------------------------------------------------ Arena
def test_arena_pairs_strong_with_weak():
    players = [PlayerInfo(i, (), float(i * 100)) for i in range(8)]  # 0..700
    result = build_teams(players, MODE_ARENA, seed=3)
    assert len(result.teams) == 4
    assert all(t.size == 2 for t in result.teams)
    assert {t.total_score for t in result.teams} == {700.0}


def test_arena_max_eight_duos_per_match():
    players = random_players(21, seed=8, with_roles=False)
    result = build_teams(players, MODE_ARENA, seed=8)
    assert len(result.teams) == 10
    assert result.substitutes == [players[20].discord_id]
    counts = Counter(t.match_index for t in result.teams)
    assert sorted(counts.values()) == [5, 5]
    assert all(v <= 8 for v in counts.values())


# ------------------------------------------------------------------ général
def test_unknown_ranks_are_supported():
    players = [PlayerInfo(i, [LANE_ROLES[i % 5]], None) for i in range(10)]
    result = build_teams(players, MODE_SR, seed=1)
    assert result.teams[0].total_score == result.teams[1].total_score == 5 * DEFAULT_RANK_SCORE


def test_not_enough_players():
    with pytest.raises(NotEnoughPlayersError) as exc:
        build_teams(random_players(7), MODE_SR)
    assert exc.value.required == 10


def test_duplicates_are_ignored():
    players = random_players(10)
    result = build_teams(players + players[:3], MODE_SR, seed=0)
    assert result.substitutes == []
    assert len(ids_of(result)) == 10


@pytest.mark.parametrize("mode", [MODE_SR, MODE_ARAM, MODE_ARENA])
def test_same_seed_same_result(mode):
    players = random_players(20, seed=5)
    a = build_teams(players, mode, seed=1234)
    b = build_teams(players, mode, seed=1234)
    assert [t.players for t in a.teams] == [t.players for t in b.teams]
    assert a.substitutes == b.substitutes


def test_different_seeds_give_variety_but_quality():
    players = random_players(20, seed=6)
    results = [build_teams(players, MODE_SR, seed=s) for s in range(8)]
    compositions = {tuple(sorted(tuple(sorted(t.players)) for t in r.teams)) for r in results}
    assert len(compositions) > 1
    best = min(r.cost for r in results)
    assert all(r.cost <= best + 10 for r in results)


@pytest.mark.parametrize("mode", [MODE_SR, MODE_ARAM, MODE_ARENA])
def test_performance_twenty_players(mode):
    players = random_players(20, seed=2)
    start = time.perf_counter()
    build_teams(players, mode, seed=99)
    assert time.perf_counter() - start < 0.2
