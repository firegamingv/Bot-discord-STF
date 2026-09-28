"""Groupe de commandes slash ``/inhouse``.

Chaque fichier de fonctionnalité du paquet y attache ses sous-commandes ;
``__init__.py`` ajoute le groupe à l'arbre.
"""

from __future__ import annotations

from discord import app_commands

inhouse_group = app_commands.Group(
    name="inhouse",
    description="Organiser des inhouses League of Legends (Faille, ARAM, Arena)",
    guild_only=True,
)
