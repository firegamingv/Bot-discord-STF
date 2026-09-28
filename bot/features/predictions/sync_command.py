"""``/pronos-admin synchroniser`` : lance tout de suite la synchronisation des matchs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.core.checks import organizer_only
from bot.core.errors import UserFacingError
from bot.features.predictions.group import pronos_admin_group
from bot.features.predictions.sync_service import sync_guild
from bot.repositories.competitions import SOURCE_LOLESPORTS, CompetitionRepository
from bot.repositories.matches import MatchRepository
from bot.utils import embeds
from bot.utils.embeds import Colors

if TYPE_CHECKING:
    from bot.core.bot import STFBot


@pronos_admin_group.command(name="synchroniser", description="🔄 Mettre à jour maintenant les matchs et résultats LoL Esports")
@organizer_only()
async def sync_now(interaction: discord.Interaction) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild_id: int = interaction.guild_id  # type: ignore[assignment]
    comps = await CompetitionRepository(bot.db).list(guild_id, source=SOURCE_LOLESPORTS)
    if not comps:
        raise UserFacingError(
            "Aucune compétition LoL Esports n'est suivie sur ce serveur. "
            "Choisis-en avec `/pronos-admin competitions`, puis relance la synchronisation."
        )
    await interaction.response.defer(ephemeral=True, thinking=True)
    await MatchRepository(bot.db).mark_live_started(guild_id)
    report = await sync_guild(bot, guild_id)
    embed = discord.Embed(
        title="🔄 Synchronisation terminée" if not report.errors else "⚠️ Synchronisation partielle",
        color=Colors.SUCCESS if not report.errors else Colors.WARNING,
    )
    embed.add_field(name="🏆 Compétitions", value=", ".join(c.name for c in comps)[:1024], inline=False)
    embed.add_field(name="🆕 Nouveaux matchs", value=str(report.created), inline=True)
    embed.add_field(name="✏️ Mis à jour", value=str(report.updated), inline=True)
    embed.add_field(name="💰 Matchs réglés", value=str(report.settled), inline=True)
    if report.errors:
        embed.add_field(name="Problèmes", value="\n".join(f"• {e}" for e in report.errors)[:1024], inline=False)
    embed.set_footer(text="La synchronisation tourne aussi automatiquement toutes les 10 minutes.")
    await embeds.reply(interaction, embed)
