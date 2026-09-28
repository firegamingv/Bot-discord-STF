"""``/compte actualiser`` : forcer la mise à jour du rang et du Riot ID depuis l'API Riot."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.errors import ExternalServiceError, NotFoundError
from bot.features.account.display import rank_line
from bot.features.account.group import account_group
from bot.features.account.linking_service import link_riot_account
from bot.features.account.rank_refresh import refresh_account
from bot.repositories.riot_accounts import RiotAccountRepository
from bot.utils.embeds import Colors

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


@account_group.command(name="actualiser", description="Mettre à jour ton rang et ton Riot ID depuis les serveurs Riot")
@app_commands.checks.cooldown(1, 60, key=lambda i: i.user.id)
async def refresh_command(interaction: discord.Interaction) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    repo = RiotAccountRepository(bot.db)
    before = await repo.get(interaction.user.id)
    if before is None:
        raise NotFoundError("Tu n'as pas encore lié de compte. Commence par `/compte lier riot_id:Pseudo#TAG`.")
    if not bot.riot.enabled:
        raise ExternalServiceError(
            "L'API Riot n'est pas configurée sur ce bot : impossible d'actualiser ton rang pour le moment. "
            "Préviens un admin."
        )

    await interaction.response.defer(ephemeral=True, thinking=True)
    if before.verified:
        after = await refresh_account(bot, before, refresh_riot_id=True, use_cache=False)
        verified_now = False
    else:
        # Liaison faite en mode dégradé : on en profite pour vérifier le compte.
        after = await link_riot_account(bot, interaction.user, before.game_name, before.tag_line)
        verified_now = True

    embed = discord.Embed(title="🔄 Compte actualisé", color=Colors.SUCCESS)
    riot_id = discord.utils.escape_markdown(after.riot_id)
    if after.riot_id != before.riot_id:
        embed.add_field(
            name="Riot ID",
            value=f"~~{discord.utils.escape_markdown(before.riot_id)}~~ → **{riot_id}**",
            inline=False,
        )
    else:
        embed.add_field(name="Riot ID", value=f"**{riot_id}**", inline=False)
    rank_value = rank_line(after, with_date=True)
    if before.rank_label != after.rank_label and before.rank_tier:
        rank_value = f"{before.rank_label} → {rank_value}"
    embed.add_field(name="Rang Solo/Duo", value=rank_value, inline=False)
    if verified_now:
        embed.description = "✅ Ton compte est maintenant **vérifié** auprès de Riot."
    log.info("Compte de %s (%s) actualisé : %s, %s", interaction.user, interaction.user.id,
             after.riot_id, after.rank_label)
    await interaction.followup.send(embed=embed, ephemeral=True)
