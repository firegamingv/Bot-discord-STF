"""Gestion des événements (groupe ``/evenement``) : le cœur du bot.

Un fichier par fonctionnalité :

| Fichier | Rôle |
|---|---|
| ``group.py`` | groupe slash ``/evenement`` |
| ``kinds.py`` | types d'événements (hooks pour inhouse & co) |
| ``autocomplete.py`` | autocomplétion + ``resolve_event`` |
| ``announcement.py`` | embed / boutons / publication / mise à jour de l'annonce |
| ``buttons.py`` | boutons persistants Rejoindre / Quitter / Voir les inscrits |
| ``registration_service.py`` | logique d'inscription / désinscription (verrou, liste d'attente) |
| ``notifications.py`` | MP aux membres (fallback mention dans le salon) |
| ``create.py`` / ``edit.py`` / ``delete.py`` | créer / modifier / supprimer |
| ``listing.py`` / ``details.py`` | liste paginée / détails |
| ``participants.py`` | voir les inscrits, retirer un membre |
| ``self_registration.py`` | ``/evenement rejoindre`` / ``quitter`` |
| ``registration_toggle.py`` | ouvrir / fermer les inscriptions |
| ``manual_announcement.py`` | annonce manuelle (mention d'un rôle, republication) |
| ``finish.py`` | clôture manuelle |
| ``reminders_task.py`` | rappels automatiques avant le début |
| ``lifecycle_task.py`` | passage automatique en cours / terminé |
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Import des fichiers de commandes : chacun attache ses sous-commandes à ``event_group``.
from bot.features.events import (  # noqa: F401
    create,
    delete,
    details,
    edit,
    finish,
    listing,
    manual_announcement,
    participants,
    registration_toggle,
    self_registration,
)
from bot.features.events.buttons import JoinButton, LeaveButton, ParticipantsButton
from bot.features.events.group import event_group
from bot.features.events.lifecycle_task import EventLifecycleTask
from bot.features.events.reminders_task import EventRemindersTask

if TYPE_CHECKING:
    from bot.core.bot import STFBot


async def setup(bot: "STFBot") -> None:
    bot.tree.add_command(event_group)
    bot.add_dynamic_items(JoinButton, LeaveButton, ParticipantsButton)
    await bot.add_cog(EventRemindersTask(bot))
    await bot.add_cog(EventLifecycleTask(bot))


async def teardown(bot: "STFBot") -> None:
    bot.tree.remove_command(event_group.name)
    bot.remove_dynamic_items(JoinButton, LeaveButton, ParticipantsButton)
