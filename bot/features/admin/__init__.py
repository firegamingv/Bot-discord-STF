"""Domaine « configuration » : groupe ``/config`` réservé aux organisateurs.

Fichiers :
- ``group.py``               : groupe slash ``/config``
- ``channels.py``            : ``/config salon-annonces`` / ``/config salon-pronos``
- ``organizer_role.py``      : ``/config role-organisateur``
- ``reminders.py``           : ``/config rappels`` (+ ``reminder_parser.py``, sans Discord)
- ``predictions_scale.py``   : ``/config bareme-pronos``
- ``overview.py``            : ``/config voir``
- ``channel_permissions.py`` : vérification des permissions du bot dans un salon
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Importer les modules de commandes attache leurs sous-commandes au groupe.
# L'ordre des imports = l'ordre d'affichage des sous-commandes dans Discord.
from bot.features.admin import channels  # noqa: F401,I001
from bot.features.admin import organizer_role  # noqa: F401
from bot.features.admin import reminders  # noqa: F401
from bot.features.admin import predictions_scale  # noqa: F401
from bot.features.admin import overview  # noqa: F401
from bot.features.admin.group import config_group

if TYPE_CHECKING:
    from bot.core.bot import STFBot


async def setup(bot: "STFBot") -> None:
    bot.tree.add_command(config_group)
