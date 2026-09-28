"""``/config voir`` : récapitulatif de tous les paramètres du serveur, avec conseils."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.core.checks import guild_only_check, organizer_only
from bot.features.admin.channel_permissions import missing_post_permissions
from bot.features.admin.group import config_group
from bot.features.admin.predictions_scale import scale_lines
from bot.features.admin.reminders import format_offsets
from bot.utils import embeds
from bot.utils.embeds import Colors, Emojis

if TYPE_CHECKING:
    from bot.core.bot import STFBot


def _channel_status(guild: discord.Guild, channel_id: int | None, command: str, tips: list[str]) -> str:
    if channel_id is None:
        tips.append(f"Choisis un salon avec `{command}`.")
        return f"{Emojis.WARNING} *Non configuré*"
    channel = guild.get_channel(channel_id)
    if channel is None:
        tips.append(f"Le salon configuré a été supprimé : choisis-en un autre avec `{command}`.")
        return f"{Emojis.ERROR} *Salon supprimé*"
    missing = missing_post_permissions(channel)
    if missing:
        tips.append(f"Dans {channel.mention}, il me manque : {', '.join(missing)}.")
        return f"{channel.mention} {Emojis.WARNING} *permissions manquantes*"
    return f"{channel.mention} {Emojis.SUCCESS}"


@config_group.command(name="voir", description="Voir tous les paramètres du bot sur ce serveur")
@organizer_only()
async def show_config(interaction: discord.Interaction) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild = guild_only_check(interaction)
    settings = await bot.settings.get(guild.id)
    tips: list[str] = []

    embed = discord.Embed(
        title=f"⚙️ Configuration de {guild.name}",
        description="Voici comment je suis réglé ici. Chaque ligne se modifie avec la commande indiquée.",
        color=Colors.PRIMARY,
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(
        name="📣 Salon des annonces · `/config salon-annonces`",
        value=_channel_status(guild, settings.announce_channel_id, "/config salon-annonces", tips),
        inline=False,
    )
    embed.add_field(
        name="🎯 Salon des pronostics · `/config salon-pronos`",
        value=_channel_status(guild, settings.predictions_channel_id, "/config salon-pronos", tips),
        inline=False,
    )

    if settings.organizer_role_id is None:
        role_value = "*Aucun* — seuls les membres avec *Gérer le serveur* organisent"
        tips.append("Tu peux déléguer l'organisation à ton staff avec `/config role-organisateur`.")
    elif (role := guild.get_role(settings.organizer_role_id)) is None:
        role_value = f"{Emojis.ERROR} *Rôle supprimé*"
        tips.append("Le rôle organisateur a été supprimé : choisis-en un autre avec `/config role-organisateur`.")
    else:
        role_value = role.mention
    embed.add_field(name="🛡️ Rôle organisateur · `/config role-organisateur`", value=role_value, inline=False)

    embed.add_field(
        name=f"{Emojis.BELL} Rappels · `/config rappels`",
        value=format_offsets(settings.reminder_offsets),
        inline=False,
    )
    embed.add_field(name="🪙 Pronostics · `/config bareme-pronos`", value=scale_lines(settings), inline=False)

    riot = getattr(bot, "riot", None)
    if riot is not None and riot.enabled:
        riot_value = f"{Emojis.SUCCESS} Connectée (serveur `{riot.platform}`) — Riot ID vérifiés et rangs à jour"
    else:
        riot_value = f"{Emojis.WARNING} Désactivée — les comptes sont liés sans vérification ni rang"
        tips.append("Ajoute `RIOT_API_KEY` dans le `.env` du bot pour vérifier les comptes et équilibrer les équipes.")
    embed.add_field(name="🔑 API Riot", value=riot_value, inline=False)

    if tips:
        embed.add_field(name="💡 Conseils", value=embeds.truncate("\n".join(f"• {t}" for t in tips)), inline=False)
        embed.color = Colors.WARNING
    else:
        embed.set_footer(text="Tout est prêt ! Lance un événement avec /evenement creer ou /inhouse creer.")
    await embeds.reply(interaction, embed)
