"""Inhouses League of Legends (groupe ``/inhouse``), branchés sur le système d'événements.

Une session = un événement de type ``inhouse`` (inscriptions, liste d'attente, rappels
génériques) + une ligne ``inhouse_sessions`` (mode, équipes).

| Fichier | Rôle |
|---|---|
| ``group.py`` | groupe slash ``/inhouse`` |
| ``constants.py`` | modes de jeu (Faille / ARAM / Arena), emojis d'équipes |
| ``kind.py`` | ``InhouseKind`` : annonce, boutons, règles d'inscription, hooks |
| ``players.py`` | fiches joueurs (Riot ID, rang, rôles, MultiGG) |
| ``autocomplete.py`` | autocomplétion des sessions / équipes, ``resolve_inhouse`` |
| ``create.py`` / ``edit.py`` | ``/inhouse creer`` / ``/inhouse modifier`` |
| ``registration.py`` | ``/inhouse inscriptions`` (ouvrir / fermer) |
| ``roster.py`` | ``/inhouse inscrits`` (détail ouvert à tous) |
| ``teams_generate.py`` | ``/inhouse equipes-generer`` (aperçu Publier / Regénérer / Annuler) |
| ``teams_adjust.py`` | ``/inhouse equipes-echanger`` / ``equipes-deplacer`` |
| ``teams_publish.py`` | ``/inhouse equipes-publier`` (publie ou met à jour le message) |
| ``teams_display.py`` | embeds des équipes + bouton persistant ``ih:teams:<id>`` |
| ``announce.py`` | ``/inhouse annoncer`` (annonce manuelle) |
| ``confirm.py`` | vue de confirmation Oui / Non |

Algorithme de constitution des équipes : ``bot/services/team_builder.py`` ;
liens MultiGG : ``bot/services/multigg.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Import des fichiers de commandes : chacun attache ses sous-commandes à ``inhouse_group``.
from bot.features.inhouse import (  # noqa: F401
    announce,
    create,
    edit,
    registration,
    roster,
    teams_adjust,
    teams_generate,
    teams_publish,
)
from bot.features.events.kinds import register_kind
from bot.features.inhouse.group import inhouse_group
from bot.features.inhouse.kind import InhouseKind
from bot.features.inhouse.teams_display import TeamsButton

if TYPE_CHECKING:
    from bot.core.bot import STFBot


async def setup(bot: "STFBot") -> None:
    register_kind(InhouseKind())
    bot.tree.add_command(inhouse_group)
    bot.add_dynamic_items(TeamsButton)


async def teardown(bot: "STFBot") -> None:
    bot.tree.remove_command(inhouse_group.name)
    bot.remove_dynamic_items(TeamsButton)
