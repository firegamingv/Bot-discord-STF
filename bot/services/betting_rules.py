"""Règles des pronostics (fonctions pures, testables sans Discord ni base).

- Types de pari : ``winner`` (vainqueur du match) et ``exact_score`` (score exact, Bo3/Bo5).
- Mise minimale ``MIN_STAKE``, maximale = solde du membre.
- Gain d'un pari gagnant = ``round(mise × cote)`` (arrondi « commercial » : 0,5 → au-dessus).
"""

from __future__ import annotations

import math
import re

from bot.core.errors import UserFacingError

MIN_STAKE = 10
DEFAULT_STAKE = 50

BET_WINNER = "winner"
BET_EXACT_SCORE = "exact_score"
BET_TYPES = (BET_WINNER, BET_EXACT_SCORE)
BET_TYPE_LABELS = {
    BET_WINNER: "Vainqueur",
    BET_EXACT_SCORE: "Score exact",
}
BET_TYPE_EMOJIS = {BET_WINNER: "🏆", BET_EXACT_SCORE: "🎯"}

# Formats pour lesquels le pari « score exact » est proposé
EXACT_SCORE_FORMATS = (3, 5)

_SCORE_RE = re.compile(r"^\s*(\d)\s*[-–:/ ]\s*(\d)\s*$")


class BetRuleError(UserFacingError):
    """Pari refusé par les règles (message destiné au membre)."""


def bet_type_label(bet_type: str | None, *, emoji: bool = True) -> str:
    if bet_type is None:
        return "Tous les paris"
    label = BET_TYPE_LABELS.get(bet_type, bet_type)
    return f"{BET_TYPE_EMOJIS.get(bet_type, '')} {label}".strip() if emoji else label


def wins_needed(best_of: int) -> int:
    """Nombre de manches à gagner : Bo1 → 1, Bo3 → 2, Bo5 → 3."""
    return best_of // 2 + 1


def exact_score_allowed(best_of: int) -> bool:
    return best_of in EXACT_SCORE_FORMATS


def possible_scores(best_of: int) -> list[str]:
    """Scores finaux possibles, du point de vue « équipe 1 - équipe 2 ».

    Bo3 → ``['2-0', '2-1', '1-2', '0-2']`` ; Bo5 → ``['3-0', '3-1', '3-2', '2-3', '1-3', '0-3']``.
    """
    need = wins_needed(best_of)
    team1 = [f"{need}-{lose}" for lose in range(0, need)]
    team2 = [f"{lose}-{need}" for lose in reversed(range(0, need))]
    return team1 + team2


def parse_score(raw: str, best_of: int) -> tuple[int, int]:
    """``"2-1"`` → ``(2, 1)`` ; lève ``BetRuleError`` si le score est impossible pour ce format."""
    m = _SCORE_RE.match(raw or "")
    if not m:
        raise BetRuleError(
            f"Score illisible : `{raw}`. Écris-le sous la forme `2-1` "
            f"(scores possibles : {', '.join(possible_scores(best_of))})."
        )
    a, b = int(m[1]), int(m[2])
    if f"{a}-{b}" not in possible_scores(best_of):
        raise BetRuleError(
            f"Le score `{a}-{b}` est impossible en Bo{best_of}. "
            f"Scores possibles : {', '.join(possible_scores(best_of))}."
        )
    return a, b


def winner_from_score(team1_score: int, team2_score: int) -> int | None:
    if team1_score > team2_score:
        return 1
    if team2_score > team1_score:
        return 2
    return None


def validate_stake(stake: int, balance: int) -> None:
    if stake < MIN_STAKE:
        raise BetRuleError(f"La mise minimale est de **{MIN_STAKE}** 🪙.")
    if stake > balance:
        raise BetRuleError(
            f"Tu n'as pas assez de points : mise **{stake}** 🪙 pour un solde de **{balance}** 🪙. "
            "Baisse ta mise ou attends ton bonus quotidien de demain !"
        )


def compute_payout(stake: int, odds: float) -> int:
    """Gain total rendu au parieur (mise incluse) : ``round(mise × cote)``, 0,5 arrondi au-dessus."""
    return int(math.floor(stake * odds + 0.5 + 1e-9))


def validate_choice(bet_type: str, choice: str, best_of: int) -> str:
    """Normalise le choix d'un pari (``"1"``/``"2"`` ou un score ``"2-1"``)."""
    if bet_type == BET_WINNER:
        if choice not in ("1", "2"):
            raise BetRuleError("Choisis l'équipe 1 ou l'équipe 2.")
        return choice
    if bet_type == BET_EXACT_SCORE:
        if not exact_score_allowed(best_of):
            raise BetRuleError(
                f"Le pari sur le score exact n'existe que pour les Bo3 et Bo5 (ce match est un Bo{best_of}). "
                "Parie plutôt sur le vainqueur !"
            )
        a, b = parse_score(choice, best_of)
        return f"{a}-{b}"
    raise BetRuleError(f"Type de pari inconnu : `{bet_type}`.")


def is_winning_bet(bet_type: str, choice: str, *, winner: int | None,
                   team1_score: int | None, team2_score: int | None) -> bool:
    if winner is None:
        return False
    if bet_type == BET_WINNER:
        return choice == str(winner)
    if bet_type == BET_EXACT_SCORE:
        if team1_score is None or team2_score is None:
            return False
        return choice == f"{team1_score}-{team2_score}"
    return False


def odds_for(bet_type: str, *, odds_winner: float, odds_exact_score: float) -> float:
    return odds_exact_score if bet_type == BET_EXACT_SCORE else odds_winner
