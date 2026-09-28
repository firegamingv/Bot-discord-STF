"""Domaine « aide » : commande ``/aide`` (guide interactif des commandes)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bot.features.help.help_command import help_command

if TYPE_CHECKING:
    from bot.core.bot import STFBot


async def setup(bot: "STFBot") -> None:
    bot.tree.add_command(help_command)
