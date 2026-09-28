"""``/evenement creer`` : créer un événement et publier son annonce."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands

from bot.core.checks import organizer_only
from bot.core.errors import UserFacingError
from bot.features.events.announcement import link_button, publish_event_message, resolve_target_channel
from bot.features.events.group import event_group
from bot.repositories.events import EventRepository
from bot.utils import embeds
from bot.utils.embeds import Colors
from bot.utils.time import DATE_HELP, DateParseError, discord_full, now_utc, parse_user_datetime

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


def parse_event_date(bot: "STFBot", raw: str) -> datetime:
    """Date saisie (heure locale du serveur) -> datetime UTC, forcément dans le futur."""
    try:
        starts_at = parse_user_datetime(raw, bot.config.timezone)
    except DateParseError as exc:
        message = str(exc)
        if DATE_HELP not in message:
            message = f"{message} {DATE_HELP}"
        raise UserFacingError(message, title="Date non reconnue 🤔") from exc
    if starts_at <= now_utc():
        raise UserFacingError(
            f"Cette date est déjà passée ({discord_full(starts_at)}). Choisis une date dans le futur. {DATE_HELP}",
            title="Date dans le passé",
        )
    return starts_at


@event_group.command(name="creer", description="Créer un nouvel événement et publier son annonce")
@app_commands.describe(
    titre="Nom de l'événement (ex. « Soirée jeux de société »)",
    date="Date et heure locales : 28/09 21h, demain 20h30, samedi 18h…",
    description="Détails affichés dans l'annonce (programme, lieu, règles…)",
    places="Nombre de places (vide = illimité). Au-delà : liste d'attente automatique",
    salon="Salon de l'annonce (par défaut : salon d'annonces configuré, sinon ce salon)",
    annoncer="Publier l'annonce avec les boutons d'inscription tout de suite (oui par défaut)",
)
@organizer_only()
async def create_event(
    interaction: discord.Interaction,
    titre: app_commands.Range[str, 2, 100],
    date: str,
    description: Optional[app_commands.Range[str, 1, 2000]] = None,
    places: Optional[app_commands.Range[int, 1, 500]] = None,
    salon: discord.TextChannel | discord.Thread | None = None,
    annoncer: bool = True,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    assert interaction.guild is not None  # groupe guild_only
    starts_at = parse_event_date(bot, date)

    # On vérifie le salon AVANT de créer quoi que ce soit : pas d'événement fantôme.
    channel = await resolve_target_channel(bot, interaction, salon) if annoncer else None

    await interaction.response.defer(ephemeral=True, thinking=True)
    event = await EventRepository(bot.db).create(
        guild_id=interaction.guild.id,
        type="generic",
        title=titre.strip(),
        description=(description or "").strip() or None,
        starts_at=starts_at,
        max_participants=places,
        created_by=interaction.user.id,
    )
    log.info(
        "Événement %s « %s » créé par %s (%s) pour le %s",
        event.id, event.title, interaction.user, interaction.user.id, starts_at.isoformat(),
    )

    embed = discord.Embed(
        title="🎉 Événement créé !",
        description=f"**{event.title}** · #{event.id}\n📅 {discord_full(starts_at)}",
        color=Colors.SUCCESS,
    )
    embed.add_field(name="👥 Places", value=str(places) if places else "Illimitées", inline=True)
    view: discord.ui.View | None = None

    if channel is not None:
        try:
            message = await publish_event_message(bot, event, channel)  # type: ignore[arg-type]
        except discord.HTTPException as exc:
            log.warning("Publication de l'annonce %s impossible : %s", event.id, exc)
            embed.color = Colors.WARNING
            embed.add_field(
                name="⚠️ Annonce non publiée",
                value=(
                    f"Je n'ai pas pu poster dans {channel.mention}. Vérifie mes permissions puis utilise "
                    "`/evenement annoncer` pour publier l'annonce."
                ),
                inline=False,
            )
        else:
            embed.add_field(name="📣 Annonce", value=f"Publiée dans {message.channel.mention}", inline=True)  # type: ignore[union-attr]
            view = discord.ui.View()
            view.add_item(link_button(message.jump_url))
    else:
        embed.add_field(
            name="📣 Annonce",
            value="Pas encore publiée : utilise `/evenement annoncer` quand tu es prêt·e.",
            inline=False,
        )

    embed.add_field(
        name="💡 Et ensuite ?",
        value=(
            "• `/evenement modifier` pour changer le titre, la date ou les places\n"
            "• `/evenement inscriptions` pour ouvrir/fermer les inscriptions\n"
            "• Les rappels partent automatiquement avant le début ⏰"
        ),
        inline=False,
    )
    await embeds.reply(interaction, embed, ephemeral=True, view=view)
