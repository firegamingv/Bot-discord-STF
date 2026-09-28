"""``/inhouse annoncer`` : annonce publique manuelle d'une session (message + mention facultatifs).

L'annonce publiée **devient l'annonce principale** (mise à jour en direct) ; l'ancienne est
supprimée pour éviter les doublons avec des informations périmées. On peut mentionner un rôle
et/ou tous les inscrits (pratique pour un « on commence dans 5 minutes ! »).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands

from bot.core.checks import organizer_only
from bot.core.errors import UserFacingError
from bot.features.events.announcement import (
    delete_event_message,
    link_button,
    publish_event_message,
    resolve_target_channel,
)
from bot.features.inhouse.autocomplete import inhouse_autocomplete, resolve_inhouse
from bot.features.inhouse.constants import get_mode
from bot.features.inhouse.group import inhouse_group
from bot.repositories.participants import REGISTERED, ParticipantRepository
from bot.utils import embeds

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

MAX_MENTIONS = 60


@inhouse_group.command(name="annoncer", description="Publier une annonce de l'inhouse (message et mentions facultatifs)")
@app_commands.describe(
    session="La session d'inhouse (tape pour chercher)",
    message="Petit mot au-dessus de l'annonce (ex. « Plus que 2 places, venez ! »)",
    mentionner="Rôle à mentionner (facultatif)",
    inscrits="Mentionner aussi tous les inscrits (ex. « on commence ! »)",
    salon="Salon où publier (par défaut : salon d'annonces configuré, sinon ce salon)",
)
@app_commands.autocomplete(session=inhouse_autocomplete)
@organizer_only()
async def announce_inhouse(
    interaction: discord.Interaction,
    session: int,
    message: Optional[app_commands.Range[str, 1, 1500]] = None,
    mentionner: Optional[discord.Role] = None,
    inscrits: bool = False,
    salon: Optional[discord.TextChannel | discord.Thread] = None,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event, ih = await resolve_inhouse(interaction, session)
    if not event.is_active:
        raise UserFacingError(f"**{event.title}** est terminé : inutile de l'annoncer à nouveau.")
    if mentionner is not None and mentionner.is_default():
        raise UserFacingError("Je ne mentionne pas @everyone : choisis un rôle précis.")
    channel = await resolve_target_channel(bot, interaction, salon)
    mode = get_mode(ih.game_mode)

    await interaction.response.defer(ephemeral=True, thinking=True)
    registered: list[int] = []
    if inscrits:
        registered = [p.discord_id for p in await ParticipantRepository(bot.db).list(event.id, status=REGISTERED)]

    parts: list[str] = []
    if mentionner is not None:
        parts.append(mentionner.mention)
    parts.append(f"📣 {message}" if message else f"📣 **Inhouse {mode.display} : venez nombreux !**")
    content = " ".join(parts)
    if registered:
        content += "\n" + " ".join(f"<@{uid}>" for uid in registered[:MAX_MENTIONS])
    allowed = discord.AllowedMentions(
        everyone=False,
        users=[discord.Object(uid) for uid in registered[:MAX_MENTIONS]] if registered else False,
        roles=[mentionner] if mentionner is not None else False,
    )

    old = event
    sent = await publish_event_message(bot, event, channel, content=content[:2000], allowed_mentions=allowed)  # type: ignore[arg-type]
    if old.message_id is not None and old.message_id != sent.id:
        await delete_event_message(bot, old)
    log.info(
        "Annonce manuelle de l'inhouse %s par %s (%s) dans %s", event.id, interaction.user, interaction.user.id, channel.id
    )

    text = f"Annonce publiée dans {channel.mention} pour **{event.title}** 📣"
    if old.message_id is not None:
        text += "\nL'ancienne annonce a été remplacée par celle-ci (mise à jour en direct)."
    if registered:
        text += f"\n🔔 {min(len(registered), MAX_MENTIONS)} inscrit(s) mentionné(s)."
    view = discord.ui.View()
    view.add_item(link_button(sent.jump_url))
    await embeds.reply(interaction, embeds.success(text), ephemeral=True, view=view)
