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


#: Nom « invisible » des champs de continuation (suite d'une liste découpée en plusieurs champs).
CONTINUATION = "​"
#: Limite Discord : 6000 caractères au total par embed (titre, description, champs, pied…).
EMBED_TOTAL_LIMIT = 6000


def fit_embed(embed: discord.Embed, limit: int = EMBED_TOTAL_LIMIT) -> discord.Embed:
    """Garantit que l'embed respecte la limite totale de Discord (sinon HTTP 400).

    Retire d'abord les champs de continuation (en partant de la fin), puis tronque la
    description, puis le champ le plus long. Modifie et renvoie ``embed``.
    """
    while len(embed) > limit:
        fields = embed.fields
        continuations = [i for i, f in enumerate(fields) if f.name == CONTINUATION]
        if continuations:
            embed.remove_field(continuations[-1])
            continue
        excess = len(embed) - limit
        description = embed.description or ""
        if len(description) > excess + 1:
            embed.description = truncate(description, len(description) - excess)
            continue
        if not fields:
            break
        i = max(range(len(fields)), key=lambda k: len(fields[k].value or ""))
        value = fields[i].value or ""
        if len(value) <= excess + 1:
            embed.remove_field(i)
            continue
        embed.set_field_at(
            i, name=fields[i].name, value=truncate(value, len(value) - excess), inline=fields[i].inline
        )
    return embed


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
