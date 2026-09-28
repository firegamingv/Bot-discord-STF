"""``/evenement annoncer`` : (re)publier l'annonce d'un événement, avec un message et une mention.

- sans ``republier`` : poste une annonce supplémentaire (embed + boutons fonctionnels) ;
  si l'événement n'avait pas encore d'annonce, celle-ci devient l'annonce principale ;
- avec ``republier`` : la nouvelle annonce **remplace** l'annonce principale (l'ancienne est
  supprimée) — pratique pour la faire remonter en bas du salon.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands

from bot.core.checks import organizer_only
from bot.core.errors import UserFacingError
from bot.features.events.announcement import (
    build_event_embed,
    build_event_view,
    delete_event_message,
    link_button,
    publish_event_message,
    resolve_target_channel,
)
from bot.features.events.autocomplete import event_autocomplete, resolve_event
from bot.features.events.group import event_group
from bot.utils import embeds

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


@event_group.command(name="annoncer", description="Publier (ou republier) l'annonce d'un événement")
@app_commands.describe(
    evenement="L'événement à annoncer (tape pour chercher)",
    message="Petit mot affiché au-dessus de l'annonce (ex. « Plus que 3 places ! »)",
    mentionner="Rôle à mentionner (facultatif)",
    salon="Salon où publier (par défaut : salon d'annonces configuré, sinon ce salon)",
    republier="Remplacer l'annonce principale par celle-ci (l'ancienne est supprimée)",
)
@app_commands.autocomplete(evenement=event_autocomplete)
@organizer_only()
async def announce_event(
    interaction: discord.Interaction,
    evenement: int,
    message: Optional[app_commands.Range[str, 1, 1500]] = None,
    mentionner: Optional[discord.Role] = None,
    salon: Optional[discord.TextChannel | discord.Thread] = None,
    republier: bool = False,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event = await resolve_event(interaction, evenement)
    if not event.is_active:
        raise UserFacingError(f"**{event.title}** est terminé : inutile de l'annoncer à nouveau.")
    channel = await resolve_target_channel(bot, interaction, salon)
    if mentionner is not None and mentionner.is_default():
        raise UserFacingError("Je ne mentionne pas @everyone : choisis un rôle précis.")

    parts = []
    if mentionner is not None:
        parts.append(mentionner.mention)
    parts.append(f"📣 {message}" if message else "📣 **Nouvel événement, venez nombreux !**")
    content = " ".join(parts)
    allowed = discord.AllowedMentions(
        everyone=False, users=False, roles=[mentionner] if mentionner is not None else False
    )

    await interaction.response.defer(ephemeral=True, thinking=True)
    becomes_main = republier or event.message_id is None
    if becomes_main:
        old = event
        sent = await publish_event_message(bot, event, channel, content=content, allowed_mentions=allowed)  # type: ignore[arg-type]
        if old.message_id is not None and old.message_id != sent.id:
            await delete_event_message(bot, old)
        what = "L'annonce principale a été publiée"
    else:
        embed = await build_event_embed(bot, event)
        view = await build_event_view(bot, event)
        sent = await channel.send(content=content, embed=embed, view=view, allowed_mentions=allowed)  # type: ignore[union-attr]
        what = "Annonce supplémentaire publiée"
    log.info(
        "Annonce manuelle de l'événement %s par %s (%s) dans %s (principale : %s)",
        event.id, interaction.user, interaction.user.id, channel.id, becomes_main,
    )

    text = f"{what} dans {channel.mention} pour **{event.title}** 📣"
    if mentionner is not None and not mentionner.mentionable and interaction.guild is not None:
        if not channel.permissions_for(interaction.guild.me).mention_everyone:
            text += (
                f"\n⚠️ Le rôle {mentionner.mention} n'est pas mentionnable et je n'ai pas la permission "
                "*Mentionner @everyone* : ses membres n'ont peut-être pas été notifiés."
            )
    if not becomes_main:
        text += "\n💡 Seule l'annonce principale est mise à jour en direct ; utilise `republier` pour la remplacer."
    view_link = discord.ui.View()
    view_link.add_item(link_button(sent.jump_url))
    await embeds.reply(interaction, embeds.success(text), ephemeral=True, view=view_link)
