"""Constantes des inhouses : modes de jeu, emojis d'équipes, choix des commandes."""

from __future__ import annotations

from dataclasses import dataclass

from discord import app_commands

from bot.repositories.player_roles import LOL_ROLE_EMOJIS, LOL_ROLES
from bot.services.team_builder import LANE_ROLES, MAX_TEAMS_PER_MATCH

EVENT_TYPE = "inhouse"


@dataclass(frozen=True, slots=True)
class GameMode:
    key: str
    label: str            # « Faille de l'invocateur »
    short: str            # « Faille »
    emoji: str
    team_size: int
    default_max: int      # places par défaut à la création
    format_text: str      # « 5 contre 5 avec rôles »
    uses_roles: bool

    @property
    def display(self) -> str:
        return f"{self.emoji} {self.label}"

    @property
    def min_players(self) -> int:
        return 2 * self.team_size

    @property
    def players_per_match(self) -> int:
        return self.team_size * MAX_TEAMS_PER_MATCH.get(self.key, 2)


GAME_MODES: dict[str, GameMode] = {
    "sr": GameMode("sr", "Faille de l'invocateur", "Faille", "🗺️", 5, 10, "5 contre 5 · un rôle par joueur", True),
    "aram": GameMode("aram", "ARAM", "ARAM", "❄️", 5, 10, "5 contre 5 · Abîme hurlant, champions aléatoires", False),
    "arena": GameMode("arena", "Arena", "Arena", "🏟️", 2, 16, "duos · jusqu'à 8 duos par partie", False),
}
DEFAULT_MODE = "sr"


def get_mode(key: str | None) -> GameMode:
    return GAME_MODES.get(key or DEFAULT_MODE, GAME_MODES[DEFAULT_MODE])


MODE_CHOICES = [app_commands.Choice(name=f"{m.emoji} {m.label}", value=m.key) for m in GAME_MODES.values()]

ROLE_CHOICES = [
    app_commands.Choice(name=f"{LOL_ROLE_EMOJIS[r]} {LOL_ROLES[r]}", value=r) for r in LANE_ROLES
]

# ------------------------------------------------------------------ équipes
SIDE_EMOJIS = ("🔵", "🔴")
SIDE_NAMES = ("bleu", "rouge")
DUO_EMOJIS = ("🟥", "🟧", "🟨", "🟩", "🟦", "🟪", "🟫", "⬜")
SUBSTITUTES_EMOJI = "🪑"


def team_name(index: int, mode: str = DEFAULT_MODE) -> str:
    """Nom lisible (1-based) : « Équipe 3 » ou « Duo 3 »."""
    return f"{'Duo' if mode == 'arena' else 'Équipe'} {index + 1}"


def team_badge(index: int, mode: str = DEFAULT_MODE) -> str:
    if mode == "arena":
        return DUO_EMOJIS[index % len(DUO_EMOJIS)]
    return SIDE_EMOJIS[index % 2]


def team_title(index: int, mode: str = DEFAULT_MODE) -> str:
    return f"{team_badge(index, mode)} {team_name(index, mode)}"
