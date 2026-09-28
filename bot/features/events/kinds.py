"""Registre des types d'événements.

Le système d'événements est générique ; un type particulier (ex. Inhouse LoL) s'y branche
en sous-classant ``EventKind`` et en l'enregistrant avec ``register_kind`` dans la fonction
``setup`` de son extension. Le module d'événements appelle ensuite ces « hooks » aux
bons moments (affichage, inscription, désinscription, suppression).

Pour ajouter un nouveau jeu / type d'événement : créer un paquet dans ``bot/features``,
sous-classer ``EventKind``, l'enregistrer. Rien d'autre à modifier.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.utils.embeds import Colors

if TYPE_CHECKING:
    from bot.core.bot import STFBot
    from bot.repositories.events import Event
    from bot.repositories.participants import Participant


class EventKind:
    key: str = "generic"
    label: str = "Événement"
    emoji: str = "📅"
    color: discord.Color = Colors.PRIMARY

    async def build_embed(
        self, bot: "STFBot", event: "Event", participants: list["Participant"]
    ) -> discord.Embed | None:
        """Embed de l'annonce. ``None`` = utiliser l'embed générique."""
        return None

    async def build_participants_embed(self, bot: "STFBot", event: "Event") -> discord.Embed | None:
        """Liste détaillée des inscrits (bouton « Voir les inscrits »). ``None`` = affichage générique."""
        return None

    async def extra_components(self, bot: "STFBot", event: "Event") -> list[discord.ui.Item]:
        """Boutons supplémentaires ajoutés sous l'annonce (ex. « Mes rôles »)."""
        return []

    async def check_can_join(
        self, bot: "STFBot", interaction: discord.Interaction, event: "Event"
    ) -> None:
        """Lever ``UserFacingError`` pour refuser une inscription (ex. compte LoL non lié)."""

    async def on_joined(
        self, bot: "STFBot", interaction: discord.Interaction, event: "Event", participant: "Participant"
    ) -> str | None:
        """Après l'inscription. Peut renvoyer un texte ajouté au message de confirmation."""
        return None

    async def on_left(self, bot: "STFBot", event: "Event", discord_id: int) -> None:
        """Après une désinscription (ex. retirer le joueur des équipes)."""

    async def on_deleted(self, bot: "STFBot", event: "Event") -> None:
        """Avant la suppression définitive de l'événement (nettoyage de messages annexes)."""


_KINDS: dict[str, EventKind] = {}
_GENERIC = EventKind()


def register_kind(kind: EventKind) -> None:
    _KINDS[kind.key] = kind


def get_kind(key: str) -> EventKind:
    return _KINDS.get(key, _GENERIC)


def all_kinds() -> list[EventKind]:
    return [_GENERIC, *(_KINDS[k] for k in sorted(_KINDS) if k != _GENERIC.key)]
