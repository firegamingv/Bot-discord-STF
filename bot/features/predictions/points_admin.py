"""``/pronos-admin points membre montant raison`` : ajuster le solde d'un membre (kind='admin')."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.checks import organizer_only
from bot.core.errors import UserFacingError
from bot.features.predictions.embeds import fmt_points
from bot.features.predictions.group import pronos_admin_group
from bot.repositories.points import KIND_ADMIN, PointsRepository
from bot.repositories.users import UserRepository
from bot.utils import embeds

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


@pronos_admin_group.command(name="points", description="🛠️ Donner ou retirer des points à un membre")
@app_commands.describe(
    membre="Le membre concerné",
    montant="Points à ajouter (négatif pour en retirer, ex. -100)",
    raison="Pourquoi ? (visible dans le journal du bot)",
)
@organizer_only()
async def adjust_points(
    interaction: discord.Interaction,
    membre: discord.Member,
    montant: app_commands.Range[int, -1_000_000, 1_000_000],
    raison: app_commands.Range[str, 3, 200],
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild_id: int = interaction.guild_id  # type: ignore[assignment]
    if montant == 0:
        raise UserFacingError("Un montant de 0 ne changerait rien : indique un nombre positif ou négatif.")
    if membre.bot:
        raise UserFacingError("Les bots n'ont pas de portefeuille 🤖")
    points = PointsRepository(bot.db)
    balance = await points.balance(guild_id, membre.id)
    if balance + montant < 0:
        raise UserFacingError(
            f"{membre.mention} n'a que {fmt_points(balance)} : on ne peut pas descendre sous zéro. "
            f"Retire au maximum {balance} points."
        )
    await UserRepository(bot.db).ensure(membre.id, membre.display_name)
    await points.add(guild_id, membre.id, montant, KIND_ADMIN)
    log.info("Ajustement de points : %+d pour %s par %s (%s)", montant, membre.id, interaction.user.id, raison)
    await embeds.reply(interaction, embeds.success(
        f"{fmt_points(montant, signed=True)} pour {membre.mention} — *{raison}*\n"
        f"Nouveau solde : **{fmt_points(balance + montant)}**",
        title="🛠️ Points ajustés",
    ))
