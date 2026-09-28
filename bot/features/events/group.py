"""Groupe de commandes slash ``/evenement``.

Chaque fichier de fonctionnalité du paquet y attache ses sous-commandes
(``@event_group.command(...)``) ; ``__init__.py`` ajoute le groupe à l'arbre.
"""

from __future__ import annotations

from discord import app_commands

event_group = app_commands.Group(
    name="evenement",
    description="Créer, rejoindre et gérer les événements du serveur",
    guild_only=True,
)
