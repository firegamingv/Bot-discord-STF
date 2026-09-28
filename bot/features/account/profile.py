"""``/compte profil [membre]`` : fiche joueur (Riot ID, rang, rôles, op.gg, participation)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.features.account.display import rank_line, roles_line, verification_line
from bot.features.account.group import account_group
from bot.features.account.link import LinkAccountButton
from bot.features.account.opgg import opgg_profile_url
from bot.features.account.participation_stats import ParticipationStats, get_participation_stats
from bot.features.account.rank_refresh import DEFAULT_MAX_AGE, is_rank_stale, refresh_ranks
from bot.features.account.role_picker import RolesButton
from bot.repositories.player_roles import PlayerRoleRepository
from bot.repositories.riot_accounts import RiotAccount, RiotAccountRepository
from bot.utils.embeds import Colors

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


def _participation_text(stats: ParticipationStats) -> str:
    if stats.total == 0 and stats.waitlist == 0:
        return "*Aucune participation pour l'instant* — jette un œil à `/evenement liste` !"
    lines = [f"📅 **{stats.total}** inscription(s) aux événements"]
    if stats.inhouses:
        lines.append(f"⚔️ dont **{stats.inhouses}** inhouse(s)")
    if stats.upcoming:
        lines.append(f"⏳ **{stats.upcoming}** à venir")
    if stats.waitlist:
        lines.append(f"🕒 **{stats.waitlist}** en liste d'attente")
    return "\n".join(lines)


def build_profile_embed(
    member: discord.abc.User,
    account: RiotAccount | None,
    roles: list[str],
    stats: ParticipationStats,
    *,
    is_self: bool,
) -> discord.Embed:
    embed = discord.Embed(title=f"👤 Profil de {member.display_name}", color=Colors.INHOUSE)
    embed.set_thumbnail(url=member.display_avatar.url)

    if account is None:
        embed.color = Colors.NEUTRAL
        embed.description = (
            "Tu n'as pas encore lié ton compte League of Legends.\n"
            "Clique sur **Lier mon compte LoL** ci-dessous ou utilise `/compte lier riot_id:Pseudo#TAG`."
            if is_self
            else f"{member.mention} n'a pas encore lié de compte League of Legends."
        )
    else:
        url = opgg_profile_url(account.game_name, account.tag_line, account.platform)
        embed.add_field(
            name="Compte Riot",
            value=f"**{discord.utils.escape_markdown(account.riot_id)}**\n{verification_line(account)}",
            inline=True,
        )
        embed.add_field(name="Rang Solo/Duo", value=rank_line(account, with_date=True), inline=True)
        embed.add_field(name="Liens", value=f"[📈 Voir sur op.gg]({url})", inline=True)

    embed.add_field(
        name="Rôles préférés",
        value=roles_line(roles, empty="*Pas encore choisis* — `/compte roles`" if is_self else "*Non renseignés*"),
        inline=False,
    )
    embed.add_field(name="Participation", value=_participation_text(stats), inline=False)
    if is_self and account is not None:
        embed.set_footer(text="Rang pas à jour ? /compte actualiser • Changer de compte : /compte lier")
    return embed


@account_group.command(name="profil", description="Afficher ton profil joueur (ou celui d'un autre membre)")
@app_commands.describe(membre="Le membre dont tu veux voir le profil (toi par défaut)")
async def profile_command(interaction: discord.Interaction, membre: discord.Member | None = None) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    target: discord.abc.User = membre or interaction.user
    is_self = target.id == interaction.user.id
    await interaction.response.defer(ephemeral=True, thinking=True)

    repo = RiotAccountRepository(bot.db)
    account = await repo.get(target.id)
    if account is not None and account.verified and bot.riot.enabled and is_rank_stale(account, DEFAULT_MAX_AGE):
        await refresh_ranks(bot, [target.id], max_age=DEFAULT_MAX_AGE)  # ne lève jamais
        account = await repo.get(target.id)

    roles = await PlayerRoleRepository(bot.db).get(target.id)
    stats = await get_participation_stats(bot.db, interaction.guild_id or 0, target.id)
    embed = build_profile_embed(target, account, roles, stats, is_self=is_self)

    view: discord.ui.View | None = None
    if is_self:
        view = discord.ui.View(timeout=None)
        if account is None:
            view.add_item(LinkAccountButton())
        view.add_item(RolesButton(label="Modifier mes rôles" if roles else "Choisir mes rôles"))
    if view is not None:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.followup.send(embed=embed, ephemeral=True)
