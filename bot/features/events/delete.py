"""``/evenement supprimer`` : annuler et supprimer définitivement un événement (avec confirmation)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.checks import organizer_only
from bot.core.error_reporting import BaseView
from bot.core.errors import NotFoundError
from bot.features.events.announcement import delete_event_message, get_announcement_channel
from bot.features.events.autocomplete import any_event_autocomplete, resolve_event
from bot.features.events.group import event_group
from bot.features.events.kinds import get_kind
from bot.features.events.registration_service import event_lock
from bot.repositories.events import Event, EventRepository
from bot.repositories.participants import ParticipantRepository
from bot.utils import embeds
from bot.utils.embeds import Colors
from bot.utils.time import discord_full

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


class ConfirmDeleteView(BaseView):
    """Boutons Oui / Non, utilisables uniquement par l'auteur de la commande."""

    def __init__(self, event: Event, author_id: int) -> None:
        super().__init__(timeout=60)
        self.event = event
        self.author_id = author_id
        self.message: discord.InteractionMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=embeds.error("Seule la personne qui a lancé la commande peut confirmer."), ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(
                    embed=embeds.info("⌛ Temps écoulé : suppression annulée, rien n'a changé."), view=None
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Oui, supprimer", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(
            embed=embeds.info("⏳ Suppression en cours…"), view=None
        )
        bot: STFBot = interaction.client  # type: ignore[assignment]
        notified = await delete_event(bot, self.event.id)
        text = f"**{self.event.title}** a été supprimé."
        if notified:
            text += f" {notified} participant(s) ont été prévenu(s) dans le salon de l'annonce."
        await interaction.edit_original_response(embed=embeds.success(text, title="Événement supprimé"))
        log.info("Événement %s supprimé par %s (%s)", self.event.id, interaction.user, interaction.user.id)

    @discord.ui.button(label="Non, garder", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(
            embed=embeds.info("Ouf ! Suppression annulée, l'événement est conservé 🙂"), view=None
        )


async def delete_event(bot: "STFBot", event_id: int) -> int:
    """Supprime l'événement : hook du type, message d'annonce, avis d'annulation, base.

    Renvoie le nombre de participants prévenus.
    """
    async with event_lock(event_id):
        event = await EventRepository(bot.db).get(event_id)
        if event is None:
            raise NotFoundError("Cet événement a déjà été supprimé.")
        participants = await ParticipantRepository(bot.db).list(event_id)

        try:
            await get_kind(event.type).on_deleted(bot, event)
        except Exception:  # noqa: BLE001 - le nettoyage annexe ne doit pas bloquer la suppression
            log.exception("kind.on_deleted a échoué pour l'événement %s", event_id)

        channel = await get_announcement_channel(bot, event)
        await delete_event_message(bot, event)

        notified = 0
        if channel is not None and event.is_active:
            kind = get_kind(event.type)
            ids = [p.discord_id for p in participants]
            mentions = " ".join(f"<@{uid}>" for uid in ids[:50])
            if len(ids) > 50:
                mentions += f" (+{len(ids) - 50})"
            text = f"❌ **{kind.emoji} {event.title}** (prévu {discord_full(event.starts_at)}) est annulé."
            if mentions:
                text += f"\n{mentions}"
            try:
                await channel.send(
                    text[:2000], allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False)
                )
                notified = len(ids)
            except discord.HTTPException as exc:
                log.warning("Avis d'annulation de l'événement %s non publié : %s", event_id, exc)

        await EventRepository(bot.db).delete(event_id)
        return notified


@event_group.command(name="supprimer", description="Supprimer définitivement un événement (demande confirmation)")
@app_commands.describe(evenement="L'événement à supprimer (tape pour chercher)")
@app_commands.autocomplete(evenement=any_event_autocomplete)
@organizer_only()
async def delete_event_command(interaction: discord.Interaction, evenement: int) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event = await resolve_event(interaction, evenement)
    count = await ParticipantRepository(bot.db).count(event.id)
    kind = get_kind(event.type)

    embed = discord.Embed(
        title="🗑️ Supprimer cet événement ?",
        description=(
            f"**{kind.emoji} {event.title}** · #{event.id}\n"
            f"📅 {discord_full(event.starts_at)}\n"
            f"👥 {count} inscrit(s)\n\n"
            "L'annonce sera supprimée et les inscrits prévenus de l'annulation. "
            "**Cette action est définitive.**"
        ),
        color=Colors.ERROR,
    )
    view = ConfirmDeleteView(event, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    view.message = await interaction.original_response()
