"""Charte graphique commune : couleurs et fabriques d'embeds / réponses."""

from __future__ import annotations

import discord


class Colors:
    PRIMARY = discord.Color.from_rgb(88, 101, 242)    # blurple
    SUCCESS = discord.Color.from_rgb(67, 181, 129)
    WARNING = discord.Color.from_rgb(250, 166, 26)
    ERROR = discord.Color.from_rgb(240, 71, 71)
    INFO = discord.Color.from_rgb(52, 152, 219)
    INHOUSE = discord.Color.from_rgb(200, 155, 60)    # or LoL
    ESPORT = discord.Color.from_rgb(155, 89, 182)
    NEUTRAL = discord.Color.from_rgb(47, 49, 54)


class Emojis:
    SUCCESS = "✅"
    ERROR = "❌"
    WARNING = "⚠️"
    INFO = "ℹ️"
    CALENDAR = "📅"
    CLOCK = "⏰"
    PEOPLE = "👥"
    TROPHY = "🏆"
    COIN = "🪙"
    LOCK = "🔒"
    UNLOCK = "🔓"
    BELL = "🔔"
    SWORDS = "⚔️"
    CHART = "📊"
    LINK = "🔗"


def success(message: str, *, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=f"{Emojis.SUCCESS} {message}", color=Colors.SUCCESS)


def error(message: str, *, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=f"{Emojis.ERROR} {message}", color=Colors.ERROR)


def warning(message: str, *, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=f"{Emojis.WARNING} {message}", color=Colors.WARNING)


def info(message: str, *, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=message, color=Colors.INFO)


async def reply(
    interaction: discord.Interaction,
    embed: discord.Embed,
    *,
    ephemeral: bool = True,
    view: discord.ui.View | None = None,
) -> None:
    """Répond à une interaction, qu'elle ait déjà été différée/répondue ou non."""
    kwargs: dict = {"embed": embed, "ephemeral": ephemeral}
    if view is not None:
        kwargs["view"] = view
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


def truncate(text: str, limit: int = 1024) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def chunk_lines(lines: list[str], limit: int = 1024) -> list[str]:
    """Regroupe des lignes en blocs respectant la limite d'un champ d'embed."""
    chunks: list[str] = []
    current = ""
    for line in lines:
        line = truncate(line, limit)
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks
