"""``/compte delier`` : supprimer la liaison avec le compte Riot (avec confirmation)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from bot.core.error_reporting import BaseView
from bot.core.errors import NotFoundError
from bot.features.account.group import account_group
from bot.repositories.riot_accounts import RiotAccount, RiotAccountRepository
from bot.utils import embeds
from bot.utils.embeds import Colors

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


class ConfirmUnlinkView(BaseView):
    def __init__(self, user_id: int, account: RiotAccount) -> None:
        super().__init__(timeout=60)
        self.user_id = user_id
        self.account = account
        self.origin: discord.Interaction | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(label="Oui, délier", emoji="🔓", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bot: STFBot = interaction.client  # type: ignore[assignment]
        self.stop()
        removed = await RiotAccountRepository(bot.db).unlink(self.user_id)
        if removed:
            log.info("Compte Riot %s délié de %s (%s)", self.account.riot_id, interaction.user, self.user_id)
            embed = embeds.success(
                f"Ton compte **{discord.utils.escape_markdown(self.account.riot_id)}** n'est plus lié.\n"
                "Tes rôles préférés sont conservés. Tu peux lier un autre compte avec `/compte lier`.",
                title="Compte délié",
            )
        else:
            embed = embeds.info("Ton compte était déjà délié. Rien à faire !")
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Non, garder", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(description="👍 Rien n'a changé, ton compte reste lié.", color=Colors.NEUTRAL),
            view=None,
        )

    async def on_timeout(self) -> None:
        if self.origin is None:
            return
        try:
            await self.origin.edit_original_response(
                embed=discord.Embed(description="⌛ Confirmation expirée : ton compte reste lié.",
                                    color=Colors.NEUTRAL),
                view=None,
            )
        except discord.HTTPException:
            pass


@account_group.command(name="delier", description="Délier ton compte League of Legends de Discord")
async def unlink_command(interaction: discord.Interaction) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    account = await RiotAccountRepository(bot.db).get(interaction.user.id)
    if account is None:
        raise NotFoundError("Tu n'as aucun compte lié pour le moment. Utilise `/compte lier` pour en ajouter un.")
    view = ConfirmUnlinkView(interaction.user.id, account)
    embed = embeds.warning(
        f"Veux-tu vraiment délier **{discord.utils.escape_markdown(account.riot_id)}** ?\n"
        "Ton rang ne sera plus pris en compte pour équilibrer les équipes d'inhouse.",
        title="Délier ton compte ?",
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    view.origin = interaction
