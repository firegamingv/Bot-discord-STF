"""``/evenement modifier`` : changer le titre, la date, la description ou le nombre de places."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands

from bot.core.checks import organizer_only
from bot.core.errors import UserFacingError
from bot.features.events.announcement import (
    event_message_url,
    get_announcement_channel,
    link_button,
    refresh_event_message,
)
from bot.features.events.autocomplete import event_autocomplete, resolve_event
from bot.features.events.create import parse_event_date
from bot.features.events.group import event_group
from bot.features.events.kinds import get_kind
from bot.features.events.notifications import notify_demoted, notify_promoted
from bot.features.events.registration_service import event_lock
from bot.repositories.events import STATUS_ONGOING, STATUS_SCHEDULED, EventRepository
from bot.repositories.participants import ParticipantRepository
from bot.repositories.reminders import ReminderRepository
from bot.utils import embeds
from bot.utils.embeds import Colors
from bot.utils.time import discord_full

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

CLEAR = "-"


@event_group.command(name="modifier", description="Modifier un événement (titre, date, description, places)")
@app_commands.describe(
    evenement="L'événement à modifier (tape pour chercher)",
    titre="Nouveau titre",
    date="Nouvelle date locale : 28/09 21h, demain 20h30, samedi 18h…",
    description="Nouvelle description (« - » pour l'effacer)",
    places="Nouveau nombre de places (0 = illimité)",
)
@app_commands.autocomplete(evenement=event_autocomplete)
@organizer_only()
async def edit_event(
    interaction: discord.Interaction,
    evenement: int,
    titre: Optional[app_commands.Range[str, 2, 100]] = None,
    date: Optional[str] = None,
    description: Optional[app_commands.Range[str, 1, 2000]] = None,
    places: Optional[app_commands.Range[int, 0, 500]] = None,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event = await resolve_event(interaction, evenement)
    if not event.is_active:
        raise UserFacingError(f"**{event.title}** est terminé : il ne peut plus être modifié.")
    if titre is None and date is None and description is None and places is None:
        raise UserFacingError(
            "Tu n'as rien indiqué à modifier ! Renseigne au moins une option : "
            "`titre`, `date`, `description` ou `places`."
        )

    values: dict = {}
    changes: list[str] = []
    if titre is not None and titre.strip() != event.title:
        values["title"] = titre.strip()
        changes.append(f"**Titre** : {event.title} → {values['title']}")
    new_start = parse_event_date(bot, date) if date is not None else None
    date_changed = new_start is not None and new_start != event.starts_at
    if date_changed:
        values["starts_at"] = new_start
        changes.append(f"**Date** : {discord_full(event.starts_at)} → {discord_full(new_start)}")  # type: ignore[arg-type]
        if event.status == STATUS_ONGOING:
            # Reporté alors qu'il avait démarré : on le reprogramme et on rouvre les inscriptions.
            values["status"] = STATUS_SCHEDULED
            values["registration_open"] = True
            changes.append("**État** : en cours → programmé (inscriptions rouvertes)")
    if description is not None:
        new_desc = None if description.strip() == CLEAR else description.strip()
        if new_desc != event.description:
            values["description"] = new_desc
            changes.append("**Description** : " + ("effacée" if new_desc is None else "mise à jour"))
    capacity_changed = False
    if places is not None:
        new_max = None if places == 0 else places
        if new_max != event.max_participants:
            values["max_participants"] = new_max
            capacity_changed = True
            old = event.max_participants or "illimité"
            changes.append(f"**Places** : {old} → {new_max or 'illimité'}")

    if not values:
        await embeds.reply(
            interaction,
            embeds.info("Ces valeurs sont déjà celles de l'événement : rien à changer 🙂"),
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    promoted: list[int] = []
    demoted: list[int] = []
    async with event_lock(event.id):
        updated = await EventRepository(bot.db).update(event.id, **values)
        assert updated is not None
        if capacity_changed:
            promoted, demoted = await ParticipantRepository(bot.db).rebalance(event.id, updated.max_participants)
    if date_changed:
        await ReminderRepository(bot.db).reset(event.id)
    log.info("Événement %s modifié par %s (%s) : %s", event.id, interaction.user, interaction.user.id, list(values))

    await refresh_event_message(bot, event.id)
    if promoted:
        await notify_promoted(bot, updated, promoted)
        changes.append(f"🎉 {len(promoted)} membre(s) sorti(s) de la liste d'attente (prévenu·e·s)")
    if demoted:
        await notify_demoted(bot, updated, demoted)
        changes.append(f"⏳ {len(demoted)} membre(s) passé(s) en liste d'attente (prévenu·e·s)")

    if date_changed:
        await _announce_new_date(bot, updated)
        changes.append("⏰ Les rappels ont été reprogrammés pour la nouvelle date.")

    embed = discord.Embed(
        title=f"✏️ {updated.title} mis à jour",
        description="\n".join(f"• {c}" for c in changes)[:4000],
        color=Colors.SUCCESS,
    )
    embed.set_footer(text=f"Événement #{updated.id}")
    view = None
    if url := event_message_url(updated):
        view = discord.ui.View()
        view.add_item(link_button(url))
    await embeds.reply(interaction, embed, ephemeral=True, view=view)


async def _announce_new_date(bot: "STFBot", event) -> None:
    """Petit message public dans le salon de l'annonce quand l'événement est déplacé."""
    try:
        channel = await get_announcement_channel(bot, event)
        if channel is None:
            return
        kind = get_kind(event.type)
        text = f"📅 **{kind.emoji} {event.title}** a été déplacé : rendez-vous désormais {discord_full(event.starts_at)} !"
        if url := event_message_url(event):
            text += f"\n🔗 {url}"
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as exc:
        log.warning("Message de report de l'événement %s non publié : %s", event.id, exc)
