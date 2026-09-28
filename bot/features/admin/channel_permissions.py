"""Vérifie que le bot peut publier dans un salon (utilisé par ``/config salon-*`` et ``/config voir``)."""

from __future__ import annotations

import discord

# Permission Discord -> libellé lisible
REQUIRED_PERMISSIONS: dict[str, str] = {
    "view_channel": "Voir le salon",
    "send_messages": "Envoyer des messages",
    "embed_links": "Intégrer des liens",
}


def missing_post_permissions(channel: discord.abc.GuildChannel) -> list[str]:
    """Libellés des permissions qui manquent au bot pour publier des annonces dans ``channel``."""
    me = channel.guild.me
    if me is None:
        return []
    perms = channel.permissions_for(me)
    missing = [label for perm, label in REQUIRED_PERMISSIONS.items() if not getattr(perms, perm)]
    if isinstance(channel, discord.Thread) and not perms.send_messages_in_threads:
        missing.append("Envoyer des messages dans les fils")
    return missing


def missing_permissions_hint(channel: discord.abc.GuildChannel, missing: list[str]) -> str:
    listed = ", ".join(f"**{m}**" for m in missing)
    return (
        f"Je ne peux pas publier dans {channel.mention} : il me manque {listed}.\n"
        "👉 Ouvre les paramètres du salon > *Permissions*, ajoute mon rôle et coche ces "
        "permissions, puis relance la commande."
    )
