"""Groupe de commandes slash ``/config`` (paramètres du serveur, réservé aux organisateurs).

``default_permissions(manage_guild)`` masque le groupe aux membres sans « Gérer le serveur »
(un admin peut l'ouvrir au rôle organisateur via Paramètres du serveur > Intégrations).
Chaque commande est en plus protégée par ``organizer_only()``.
"""

from __future__ import annotations

import discord
from discord import app_commands

config_group = app_commands.Group(
    name="config",
    description="Configurer le bot sur ce serveur (salons, rôle organisateur, rappels, pronos)",
    guild_only=True,
    default_permissions=discord.Permissions(manage_guild=True),
)
