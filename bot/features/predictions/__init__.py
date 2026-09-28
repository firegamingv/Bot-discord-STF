"""Domaine « pronostics esport » (groupes ``/pronos`` et ``/pronos-admin``).

Fichiers (un par fonctionnalité) :
- ``group.py``                : groupes slash ``/pronos`` et ``/pronos-admin``
- ``autocomplete.py``         : autocomplétions (compétitions, matchs, équipes, scores, tournois)
- ``choices.py``              : listes de choix (périodes, types de pari)
- ``embeds.py``               : cartes de match, confirmations, résultats, classements, stats
- ``wallet_service.py``       : capital de départ + bonus quotidien paresseux
- ``betting_service.py``      : placer / modifier un pari (transaction)
- ``settlement_service.py``   : régler un match (gains, remboursements) + annonce du résultat
- ``sync_service.py``         : import des matchs LoL Esports
- ``leaderboard_service.py``  : construction des classements
- ``bet_buttons.py``          : boutons persistants ``bet:<match_id>:<choice>`` + modale de mise
- ``matches_command.py``      : ``/pronos matchs``
- ``bet_command.py``          : ``/pronos parier`` et ``/pronos parier-score``
- ``my_bets.py``              : ``/pronos mes-paris``
- ``balance.py``              : ``/pronos solde``
- ``stats.py``                : ``/pronos stats``
- ``leaderboard_command.py``  : ``/pronos classement`` et ``/pronos classement-general``
- ``rules.py``                : ``/pronos regles``
- ``competitions_admin.py``   : ``/pronos-admin competitions``
- ``competition_follow.py``   : ``/pronos-admin competition-suivre | competition-retirer`` (recherche par nom)
- ``sync_command.py``         : ``/pronos-admin synchroniser``
- ``manual_matches.py``       : ``/pronos-admin competition-creer | match-ajouter | resultat | annuler-match``
- ``points_admin.py``         : ``/pronos-admin points``
- ``leaderboard_autopost.py`` : ``/pronos-admin classement-auto …`` + tâche de publication
- ``sync_task.py``            : tâche de synchronisation (10 min)
- ``daily_matches_task.py``   : tâche « matchs du jour » (10:00)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Importer les modules de commandes attache leurs sous-commandes aux groupes.
# L'ordre des imports = l'ordre d'affichage des sous-commandes dans Discord.
from bot.features.predictions import matches_command  # noqa: F401,I001
from bot.features.predictions import bet_command  # noqa: F401
from bot.features.predictions import my_bets  # noqa: F401
from bot.features.predictions import balance  # noqa: F401
from bot.features.predictions import stats  # noqa: F401
from bot.features.predictions import leaderboard_command  # noqa: F401
from bot.features.predictions import rules  # noqa: F401
from bot.features.predictions import competitions_admin  # noqa: F401
from bot.features.predictions import competition_follow  # noqa: F401
from bot.features.predictions import sync_command  # noqa: F401
from bot.features.predictions import manual_matches  # noqa: F401
from bot.features.predictions import points_admin  # noqa: F401
from bot.features.predictions.bet_buttons import BetButton
from bot.features.predictions.daily_matches_task import DailyMatchesTask
from bot.features.predictions.group import pronos_admin_group, pronos_group
from bot.features.predictions.leaderboard_autopost import LeaderboardAutopostTask
from bot.features.predictions.sync_task import LolEsportsSyncTask

if TYPE_CHECKING:
    from bot.core.bot import STFBot


async def setup(bot: "STFBot") -> None:
    bot.tree.add_command(pronos_group)
    bot.tree.add_command(pronos_admin_group)
    bot.add_dynamic_items(BetButton)
    await bot.add_cog(LolEsportsSyncTask(bot))
    await bot.add_cog(DailyMatchesTask(bot))
    await bot.add_cog(LeaderboardAutopostTask(bot))
