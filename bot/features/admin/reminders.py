"""``/config rappels delais:"1j, 1h, 15min"`` : quand rappeler les inscrits avant un événement."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.checks import guild_only_check, organizer_only
from bot.core.errors import UserFacingError
from bot.features.admin.group import config_group
from bot.features.admin.reminder_parser import ReminderParseError, parse_reminder_offsets
from bot.utils import embeds
from bot.utils.time import humanize_minutes

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


def format_offsets(offsets: list[int]) -> str:
    if not offsets:
        return "🔕 Aucun rappel"
    return " • ".join(f"⏰ {humanize_minutes(m)} avant" for m in sorted(offsets, reverse=True))


@config_group.command(name="rappels", description="Choisir quand rappeler les inscrits avant un événement")
@app_commands.describe(delais="Délais séparés par des virgules, ex. « 1j, 1h, 15min » (ou « aucun »)")
@organizer_only()
async def set_reminders(interaction: discord.Interaction, delais: app_commands.Range[str, 1, 100]) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild = guild_only_check(interaction)
    try:
        offsets = parse_reminder_offsets(delais)
    except ReminderParseError as exc:
        raise UserFacingError(str(exc), title="Délais de rappel invalides") from exc

    await bot.settings.update(guild.id, reminder_offsets=offsets)
    log.info("Rappels = %s sur %s par %s", offsets, guild.id, interaction.user)
    if offsets:
        message = (
            f"Les inscrits recevront un rappel :\n{format_offsets(offsets)}\n\n"
            "-# Pris en compte dès les prochains rappels."
        )
    else:
        message = "Les rappels automatiques sont désactivés. Réactive-les avec par exemple `/config rappels delais:1h`."
    await embeds.reply(interaction, embeds.success(message, title="Rappels enregistrés"))
