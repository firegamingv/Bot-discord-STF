"""``/inhouse equipes-publier`` : publier (ou republier) les équipes dans un salon public.

- Premier affichage : nouveau message qui mentionne tous les joueurs.
- Republication : le message existant est **modifié** (pas de doublon) et un petit message
  « 🔄 équipes mises à jour » renvoie vers lui. Si un autre salon est choisi, l'ancien
  message est supprimé et un nouveau est posté.

``publish_teams(bot, event, session, channel=None)`` est réutilisé par les boutons
« 📢 Publier » des aperçus (génération et ajustements).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands

from bot.core.checks import ensure_organizer, organizer_only
from bot.core.error_reporting import BaseView
from bot.core.errors import UserFacingError
from bot.features.events.announcement import (
    ensure_can_post,
    get_announcement_channel,
    link_button,
    refresh_event_message,
)
from bot.features.inhouse.autocomplete import inhouse_autocomplete, resolve_inhouse
from bot.features.inhouse.group import inhouse_group
from bot.features.inhouse.teams_display import build_teams_embeds, load_teams_context, teams_mentions
from bot.repositories.events import Event, EventRepository
from bot.repositories.inhouse import InhouseRepository, InhouseSession
from bot.utils import embeds

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


async def _resolve_channel(
    bot: "STFBot", event: Event, session: InhouseSession, explicit: discord.abc.Messageable | None
) -> discord.abc.Messageable:
    if explicit is not None:
        return explicit
    if session.teams_channel_id is not None:
        channel = bot.get_channel(session.teams_channel_id)
        if isinstance(channel, discord.abc.Messageable):
            return channel
    channel = await get_announcement_channel(bot, event)
    if channel is None:
        raise UserFacingError(
            "Je ne sais pas où publier les équipes (l'annonce de l'inhouse est introuvable). "
            "Précise l'option `salon` de `/inhouse equipes-publier`."
        )
    return channel


async def publish_teams(
    bot: "STFBot",
    event: Event,
    session: InhouseSession,
    *,
    channel: discord.abc.Messageable | None = None,
) -> tuple[discord.Message, bool]:
    """Publie ou met à jour le message des équipes. Renvoie ``(message, nouveau_message)``."""
    ctx = await load_teams_context(bot, event, session)
    if not ctx.stored.exists:
        raise UserFacingError(
            "Aucune équipe à publier pour l'instant. Lance d'abord `/inhouse equipes-generer` 🎲"
        )
    target = await _resolve_channel(bot, event, session, channel)
    guild = bot.get_guild(event.guild_id)
    if guild is not None and isinstance(target, (discord.abc.GuildChannel, discord.Thread)):
        ensure_can_post(target, guild.me)

    items = build_teams_embeds(bot, ctx, draft=False)
    content = f"⚔️ **Les équipes de {event.title} sont prêtes !**\n{teams_mentions(ctx.stored)}"[:2000]
    repo = InhouseRepository(bot.db)
    target_id = getattr(target, "id", None)

    # 1) Republication dans le même salon : on modifie le message existant.
    if session.teams_message_id is not None and session.teams_channel_id == target_id:
        try:
            message = await target.get_partial_message(session.teams_message_id).edit(  # type: ignore[attr-defined]
                content=content, embeds=items, allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.NotFound:
            log.info("Message des équipes de l'inhouse %s disparu : nouveau message", event.id)
        else:
            try:
                await target.send(
                    f"🔄 Les équipes de **{event.title}** ont été mises à jour : {message.jump_url}",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                pass
            log.info("Équipes de l'inhouse %s republiées (message %s)", event.id, message.id)
            await refresh_event_message(bot, event.id)
            return message, False

    # 2) Nouveau message (premier affichage ou changement de salon).
    message = await target.send(
        content=content,
        embeds=items,
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    old_channel_id, old_message_id = session.teams_channel_id, session.teams_message_id
    await repo.set_teams_message(event.id, message.channel.id, message.id)
    if old_message_id is not None and old_message_id != message.id and old_channel_id is not None:
        old_channel = bot.get_channel(old_channel_id)
        if old_channel is not None and hasattr(old_channel, "get_partial_message"):
            try:
                await old_channel.get_partial_message(old_message_id).delete()  # type: ignore[attr-defined]
            except discord.HTTPException:
                pass
    log.info("Équipes de l'inhouse %s publiées (salon %s, message %s)", event.id, message.channel.id, message.id)
    await refresh_event_message(bot, event.id)
    return message, True


def published_embed(message: discord.Message, created: bool) -> discord.Embed:
    where = getattr(message.channel, "mention", "le salon")
    if created:
        return embeds.success(f"Équipes publiées dans {where} ! Les joueurs ont été mentionnés 📣")
    return embeds.success(
        f"Message des équipes mis à jour dans {where} 🔄 (un petit message signale la mise à jour)."
    )


class PublishTeamsView(BaseView):
    """Bouton « 📢 Publier / Republier » sous un aperçu d'équipes (réservé à l'auteur)."""

    def __init__(self, event_id: int, author_id: int, *, republish: bool) -> None:
        super().__init__(timeout=900)
        self.event_id = event_id
        self.author_id = author_id
        self.publish.label = "Republier les équipes" if republish else "Publier les équipes"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=embeds.error("Seule la personne qui a lancé la commande peut utiliser ces boutons."),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Publier les équipes", emoji="📢", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await ensure_organizer(interaction)
        bot: STFBot = interaction.client  # type: ignore[assignment]
        await interaction.response.defer()
        event = await EventRepository(bot.db).get(self.event_id)
        session = await InhouseRepository(bot.db).get(self.event_id)
        if event is None or session is None:
            raise UserFacingError("Cet inhouse n'existe plus.")
        message, created = await publish_teams(bot, event, session)
        view = discord.ui.View()
        view.add_item(link_button(message.jump_url, "Voir les équipes"))
        self.stop()
        await interaction.edit_original_response(embeds=[published_embed(message, created)], view=view)


@inhouse_group.command(name="equipes-publier", description="Publier (ou republier) les équipes dans un salon")
@app_commands.describe(
    session="La session d'inhouse (tape pour chercher)",
    salon="Salon où publier (par défaut : celui des équipes déjà publiées, sinon celui de l'annonce)",
)
@app_commands.autocomplete(session=inhouse_autocomplete)
@organizer_only()
async def publish_teams_command(
    interaction: discord.Interaction,
    session: int,
    salon: Optional[discord.TextChannel | discord.Thread] = None,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event, ih = await resolve_inhouse(interaction, session)
    await interaction.response.defer(ephemeral=True, thinking=True)
    message, created = await publish_teams(bot, event, ih, channel=salon)
    view = discord.ui.View()
    view.add_item(link_button(message.jump_url, "Voir les équipes"))
    await embeds.reply(interaction, published_embed(message, created), ephemeral=True, view=view)
