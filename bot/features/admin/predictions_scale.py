"""``/config bareme-pronos`` : points quotidiens, points de départ et cotes des pronostics."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.checks import guild_only_check, organizer_only
from bot.features.admin.group import config_group
from bot.repositories.settings import GuildSettings
from bot.utils import embeds
from bot.utils.embeds import Colors, Emojis

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

# Bornes raisonnables (garde-fous contre les fautes de frappe)
DAILY_MAX = 10_000
STARTING_MAX = 100_000
ODDS_WINNER_RANGE = (1.01, 20.0)
ODDS_EXACT_RANGE = (1.01, 50.0)


def scale_lines(settings: GuildSettings) -> str:
    return (
        f"{Emojis.COIN} **{settings.daily_points}** points offerts chaque jour\n"
        f"🎁 **{settings.starting_points}** points de départ pour un nouveau joueur\n"
        f"🏆 Cote « vainqueur » : **×{settings.odds_winner:g}**\n"
        f"🎯 Cote « score exact » : **×{settings.odds_exact_score:g}**"
    )


@config_group.command(name="bareme-pronos", description="Régler les points et les cotes des pronostics")
@app_commands.describe(
    points_quotidiens=f"Points offerts chaque jour à chaque joueur (0 à {DAILY_MAX})",
    points_depart=f"Points reçus par un nouveau joueur (0 à {STARTING_MAX})",
    cote_vainqueur="Multiplicateur d'un pari gagnant sur le vainqueur (ex. 2 = mise ×2)",
    cote_score_exact="Multiplicateur d'un pari gagnant sur le score exact (ex. 3.5)",
)
@organizer_only()
async def set_predictions_scale(
    interaction: discord.Interaction,
    points_quotidiens: app_commands.Range[int, 0, DAILY_MAX] | None = None,
    points_depart: app_commands.Range[int, 0, STARTING_MAX] | None = None,
    cote_vainqueur: app_commands.Range[float, ODDS_WINNER_RANGE[0], ODDS_WINNER_RANGE[1]] | None = None,
    cote_score_exact: app_commands.Range[float, ODDS_EXACT_RANGE[0], ODDS_EXACT_RANGE[1]] | None = None,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild = guild_only_check(interaction)

    changes: dict[str, int | float] = {}
    if points_quotidiens is not None:
        changes["daily_points"] = points_quotidiens
    if points_depart is not None:
        changes["starting_points"] = points_depart
    if cote_vainqueur is not None:
        changes["odds_winner"] = round(cote_vainqueur, 2)
    if cote_score_exact is not None:
        changes["odds_exact_score"] = round(cote_score_exact, 2)

    if not changes:
        current = await bot.settings.get(guild.id)
        embed = discord.Embed(title="🎯 Barème actuel des pronostics", description=scale_lines(current),
                              color=Colors.ESPORT)
        embed.set_footer(text="Pour modifier : renseigne au moins une option de /config bareme-pronos")
        await embeds.reply(interaction, embed)
        return

    settings = await bot.settings.update(guild.id, **changes)
    log.info("Barème des pronos modifié sur %s par %s : %s", guild.id, interaction.user, changes)
    embed = embeds.success(scale_lines(settings), title="Barème des pronostics mis à jour")
    notes: list[str] = []
    if "odds_winner" in changes or "odds_exact_score" in changes:
        notes.append("Les nouvelles cotes s'appliquent aux **prochains paris** ; les paris déjà placés gardent leur cote.")
    if "starting_points" in changes:
        notes.append("Les points de départ ne concernent que les **nouveaux** joueurs.")
    if settings.odds_exact_score <= settings.odds_winner:
        notes.append(
            f"{Emojis.WARNING} La cote « score exact » est inférieure ou égale à la cote « vainqueur » : "
            "parier sur le score exact (plus difficile) ne rapporte pas plus. Est-ce voulu ?"
        )
    if notes:
        embed.add_field(name="💡 À noter", value="\n".join(notes), inline=False)
    await embeds.reply(interaction, embed)
