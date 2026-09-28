"""Listes de choix réutilisées par plusieurs commandes (périodes, types de pari)."""

from __future__ import annotations

from discord import app_commands

from bot.services.betting_rules import BET_EXACT_SCORE, BET_WINNER
from bot.services.periods import PERIOD_ALL, PERIOD_DAY, PERIOD_MONTH, PERIOD_WEEK

PERIOD_CHOICES = [
    app_commands.Choice(name="📆 Aujourd'hui", value=PERIOD_DAY),
    app_commands.Choice(name="🗓️ Cette semaine", value=PERIOD_WEEK),
    app_commands.Choice(name="📅 Ce mois-ci", value=PERIOD_MONTH),
    app_commands.Choice(name="♾️ Depuis toujours", value=PERIOD_ALL),
]

BET_TYPE_CHOICES = [
    app_commands.Choice(name="🏆 Vainqueur", value=BET_WINNER),
    app_commands.Choice(name="🎯 Score exact", value=BET_EXACT_SCORE),
]
