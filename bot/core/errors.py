"""Exceptions métier.

Lever une ``UserFacingError`` n'importe où dans une commande affiche son message à
l'utilisateur (en éphémère) sans polluer les logs d'erreurs.
"""

from __future__ import annotations

from discord import app_commands


class UserFacingError(app_commands.AppCommandError):
    """Erreur attendue dont le message est destiné à l'utilisateur."""

    def __init__(self, message: str, *, title: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.title = title


class NotFoundError(UserFacingError):
    """Ressource introuvable (événement, match…)."""


class PermissionDeniedError(UserFacingError):
    """L'utilisateur n'a pas les droits nécessaires."""


class ExternalServiceError(UserFacingError):
    """Une API externe (Riot, LoL Esports) est indisponible ou a renvoyé une erreur."""
