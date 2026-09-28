"""Groupes de commandes slash des pronostics.

- ``/pronos``       : pour tous les membres (parier, solde, stats, classements…).
- ``/pronos-admin`` : pour les organisateurs (compétitions suivies, matchs manuels,
  résultats, classements automatiques…). Masqué par défaut aux membres sans la permission
  *Gérer le serveur* (réglable dans Paramètres du serveur → Intégrations) et chaque
  sous-commande vérifie en plus ``organizer_only()`` (rôle organisateur accepté).
"""

from __future__ import annotations

import discord
from discord import app_commands

pronos_group = app_commands.Group(
    name="pronos",
    description="🔮 Pronostics esport LoL : parie tes points sur les matchs du jour !",
    guild_only=True,
)

pronos_admin_group = app_commands.Group(
    name="pronos-admin",
    description="🛠️ Gérer les pronostics : compétitions, matchs, résultats, classements",
    guild_only=True,
    default_permissions=discord.Permissions(manage_guild=True),
)
