import pytest

from bot.services.betting_rules import (
    BET_EXACT_SCORE,
    BET_WINNER,
    BetRuleError,
    compute_payout,
    exact_score_allowed,
    is_winning_bet,
    odds_for,
    parse_score,
    possible_scores,
    validate_choice,
    validate_stake,
    winner_from_score,
    wins_needed,
)


def test_possible_scores():
    assert possible_scores(1) == ["1-0", "0-1"]
    assert possible_scores(3) == ["2-0", "2-1", "1-2", "0-2"]
    assert possible_scores(5) == ["3-0", "3-1", "3-2", "2-3", "1-3", "0-3"]
    assert wins_needed(5) == 3


def test_exact_score_only_bo3_bo5():
    assert exact_score_allowed(3) and exact_score_allowed(5)
    assert not exact_score_allowed(1)
    with pytest.raises(BetRuleError):
        validate_choice(BET_EXACT_SCORE, "1-0", 1)


def test_parse_score():
    assert parse_score("2-1", 3) == (2, 1)
    assert parse_score(" 0 : 3 ", 5) == (0, 3)
    with pytest.raises(BetRuleError):
        parse_score("2-2", 3)
    with pytest.raises(BetRuleError):
        parse_score("abc", 3)


def test_validate_choice():
    assert validate_choice(BET_WINNER, "1", 1) == "1"
    assert validate_choice(BET_EXACT_SCORE, "3:1", 5) == "3-1"
    with pytest.raises(BetRuleError):
        validate_choice(BET_WINNER, "3", 1)
    with pytest.raises(BetRuleError):
        validate_choice("autre", "1", 1)


def test_validate_stake():
    validate_stake(10, 10)
    with pytest.raises(BetRuleError):
        validate_stake(9, 100)
    with pytest.raises(BetRuleError):
        validate_stake(101, 100)


def test_payout_rounding():
    assert compute_payout(100, 2.0) == 200
    assert compute_payout(15, 3.5) == 53  # 52.5 arrondi au-dessus
    assert compute_payout(33, 1.5) == 50  # 49.5 → 50
    assert compute_payout(10, 1.33) == 13


def test_winning_bets():
    kw = dict(winner=1, team1_score=2, team2_score=1)
    assert is_winning_bet(BET_WINNER, "1", **kw)
    assert not is_winning_bet(BET_WINNER, "2", **kw)
    assert is_winning_bet(BET_EXACT_SCORE, "2-1", **kw)
    assert not is_winning_bet(BET_EXACT_SCORE, "2-0", **kw)
    assert not is_winning_bet(BET_WINNER, "1", winner=None, team1_score=None, team2_score=None)


def test_misc():
    assert winner_from_score(1, 3) == 2
    assert winner_from_score(1, 1) is None
    assert odds_for(BET_EXACT_SCORE, odds_winner=2, odds_exact_score=3.5) == 3.5
    assert odds_for(BET_WINNER, odds_winner=2, odds_exact_score=3.5) == 2
