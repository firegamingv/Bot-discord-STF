"""Domaine « compte » : liaison Riot ID, rôles préférés, profil joueur (groupe ``/compte``).

Fichiers :
- ``group.py``              : groupe slash ``/compte``
- ``link.py``               : ``/compte lier`` + modale + bouton persistant ``acc:link``
- ``unlink.py``             : ``/compte delier`` (avec confirmation)
- ``roles.py``              : ``/compte roles``
- ``role_picker.py``        : sélecteur de rôles + bouton persistant ``acc:roles``
- ``profile.py``            : ``/compte profil``
- ``refresh.py``            : ``/compte actualiser``
- ``linking_service.py``    : logique de liaison (API publique)
- ``rank_refresh.py``       : rafraîchissement des rangs (API publique)
- ``display.py`` / ``opgg.py`` / ``participation_stats.py`` : helpers d'affichage et de lecture
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Importer les modules de commandes attache leurs sous-commandes au groupe.
# L'ordre des imports = l'ordre d'affichage des sous-commandes dans Discord.
from bot.features.account import link  # noqa: F401,I001
from bot.features.account import roles  # noqa: F401
from bot.features.account import profile  # noqa: F401
from bot.features.account import refresh  # noqa: F401
from bot.features.account import unlink  # noqa: F401
from bot.features.account.group import account_group
from bot.features.account.link import LinkAccountButton
from bot.features.account.role_picker import RolesButton

if TYPE_CHECKING:
    from bot.core.bot import STFBot


async def setup(bot: "STFBot") -> None:
    bot.tree.add_command(account_group)
    bot.add_dynamic_items(LinkAccountButton, RolesButton)
