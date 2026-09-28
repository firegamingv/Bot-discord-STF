"""``/compte lier`` : lier son compte League of Legends (Riot ID) à son compte Discord.

Expose aussi :
- ``LinkAccountModal`` : modale « Riot ID » réutilisable ;
- ``LinkAccountButton`` : bouton persistant ``acc:link`` (placé sous les annonces d'inhouse).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.error_reporting import BaseModal, interaction_guard
from bot.features.account.display import rank_line, roles_line, verification_line
from bot.features.account.group import account_group
from bot.features.account.linking_service import link_riot_account, parse_riot_id
from bot.features.account.opgg import opgg_profile_url
from bot.features.account.role_picker import RolesButton
from bot.repositories.player_roles import PlayerRoleRepository
from bot.repositories.riot_accounts import RiotAccount, RiotAccountRepository
from bot.utils.embeds import Colors, Emojis

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


def build_link_success_embed(bot: "STFBot", account: RiotAccount, roles: list[str]) -> discord.Embed:
    riot_id = discord.utils.escape_markdown(account.riot_id)
    embed = discord.Embed(
        title=f"{Emojis.LINK} Compte lié !",
        description=f"Ton compte Discord est maintenant associé à **{riot_id}**.",
        color=Colors.SUCCESS if account.verified else Colors.WARNING,
        url=opgg_profile_url(account.game_name, account.tag_line, account.platform),
    )
    embed.add_field(name="Statut", value=verification_line(account), inline=True)
    embed.add_field(name="Rang Solo/Duo", value=rank_line(account), inline=True)
    embed.add_field(
        name="Rôles préférés",
        value=roles_line(roles, empty="*Pas encore choisis* 👇"),
        inline=False,
    )
    tips: list[str] = []
    if not roles:
        tips.append("🎮 **Dernière étape :** choisis tes rôles avec le bouton ci-dessous pour des équipes équilibrées.")
    else:
        tips.append("🎮 Tu peux modifier tes rôles avec le bouton ci-dessous ou `/compte roles`.")
    if not account.verified:
        tips.append(
            "⚠️ L'API Riot n'est pas disponible : ton Riot ID n'a pas pu être vérifié et ton rang "
            "est inconnu. Un `/compte actualiser` plus tard réglera ça."
        )
    tips.append("👤 Consulte ton profil à tout moment avec `/compte profil`.")
    embed.add_field(name="Et ensuite ?", value="\n".join(tips), inline=False)
    return embed


def after_link_view(has_roles: bool) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(RolesButton(label="Modifier mes rôles" if has_roles else "Choisir mes rôles"))
    return view


async def link_and_reply(interaction: discord.Interaction, game_name: str, tag_line: str | None) -> None:
    """Enchaînement commun (commande / modale) : lie le compte puis répond en éphémère."""
    bot: STFBot = interaction.client  # type: ignore[assignment]
    name, tag = parse_riot_id(game_name, tag_line)  # validation immédiate, avant l'appel réseau
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    account = await link_riot_account(bot, interaction.user, name, tag)
    roles = await PlayerRoleRepository(bot.db).get(interaction.user.id)
    await interaction.followup.send(
        embed=build_link_success_embed(bot, account, roles),
        view=after_link_view(bool(roles)),
        ephemeral=True,
    )


class LinkAccountModal(BaseModal, title="Lier mon compte League of Legends"):
    riot_id: discord.ui.TextInput = discord.ui.TextInput(
        label="Ton Riot ID (Pseudo#TAG)",
        placeholder="ex. Faker#KR1 — visible en haut du client LoL",
        min_length=5,
        max_length=23,  # 16 (pseudo) + 1 (#) + 5 (tag) + marge
    )

    def __init__(self, *, default: str | None = None) -> None:
        super().__init__()
        if default:
            self.riot_id.default = default

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await link_and_reply(interaction, self.riot_id.value, None)


class LinkAccountButton(discord.ui.DynamicItem[discord.ui.Button], template=r"acc:link"):
    """Bouton persistant « Lier mon compte LoL » (``acc:link``) : ouvre ``LinkAccountModal``."""

    def __init__(self, *, label: str = "Lier mon compte LoL", row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=label, emoji=Emojis.LINK, style=discord.ButtonStyle.secondary, custom_id="acc:link", row=row
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match, /
    ) -> "LinkAccountButton":
        return cls(label=item.label or "Lier mon compte LoL")

    async def callback(self, interaction: discord.Interaction) -> None:
        async with interaction_guard(interaction):
            bot: STFBot = interaction.client  # type: ignore[assignment]
            existing = await RiotAccountRepository(bot.db).get(interaction.user.id)
            await interaction.response.send_modal(
                LinkAccountModal(default=existing.riot_id if existing else None)
            )


@account_group.command(name="lier", description="Lier ton compte League of Legends (Riot ID) à Discord")
@app_commands.describe(riot_id="Ton Riot ID au format Pseudo#TAG (ex. Faker#KR1)")
@app_commands.checks.cooldown(3, 60, key=lambda i: i.user.id)
async def link_command(interaction: discord.Interaction, riot_id: app_commands.Range[str, 5, 30]) -> None:
    await link_and_reply(interaction, riot_id, None)
