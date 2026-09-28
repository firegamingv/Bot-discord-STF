"""``/inhouse modifier`` : changer le mode, la date, les places, le titre ou la description.

Même logique que l'édition générique des événements (liste d'attente rééquilibrée et
membres prévenus, rappels reprogrammés si la date change, annonce mise à jour), plus :
- changement de **mode** : les équipes déjà générées ne sont plus valables → confirmation,
  puis suppression de la composition et du message public des équipes ;
- si le nombre de places était celui par défaut de l'ancien mode, il suit le nouveau mode.
"""

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
from bot.features.events.create import parse_event_date
from bot.features.events.notifications import notify_demoted, notify_promoted
from bot.features.events.registration_service import event_lock
from bot.features.inhouse.autocomplete import inhouse_autocomplete, resolve_inhouse
from bot.features.inhouse.confirm import ConfirmView
from bot.features.inhouse.constants import MODE_CHOICES, get_mode
from bot.features.inhouse.create import check_capacity
from bot.features.inhouse.group import inhouse_group
from bot.repositories.events import STATUS_ONGOING, STATUS_SCHEDULED, Event, EventRepository
from bot.repositories.inhouse import InhouseRepository, InhouseSession
from bot.repositories.inhouse_teams import InhouseTeamRepository
from bot.repositories.participants import ParticipantRepository
from bot.repositories.reminders import ReminderRepository
from bot.utils import embeds
from bot.utils.embeds import Colors
from bot.utils.time import discord_full

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

CLEAR = "-"


async def _drop_teams(bot: "STFBot", session: InhouseSession) -> None:
    """Supprime la composition et le message public des équipes (changement de mode)."""
    await InhouseTeamRepository(bot.db).clear(session.event_id)
    if session.teams_channel_id and session.teams_message_id:
        channel = bot.get_channel(session.teams_channel_id)
        if channel is not None and hasattr(channel, "get_partial_message"):
            try:
                await channel.get_partial_message(session.teams_message_id).delete()  # type: ignore[attr-defined]
            except discord.HTTPException:
                pass
    await InhouseRepository(bot.db).clear_teams_message(session.event_id)


async def _announce_new_date(bot: "STFBot", event: Event) -> None:
    try:
        channel = await get_announcement_channel(bot, event)
        if channel is None:
            return
        text = f"📅 **⚔️ {event.title}** a été déplacé : rendez-vous désormais {discord_full(event.starts_at)} !"
        if url := event_message_url(event):
            text += f"\n🔗 {url}"
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as exc:
        log.warning("Message de report de l'inhouse %s non publié : %s", event.id, exc)


async def _apply(
    bot: "STFBot",
    user: discord.abc.User,
    event: Event,
    session: InhouseSession,
    values: dict,
    changes: list[str],
    *,
    new_mode: str | None,
    capacity_changed: bool,
    date_changed: bool,
    teams_exist: bool,
) -> tuple[discord.Embed, discord.ui.View | None]:
    promoted: list[int] = []
    demoted: list[int] = []
    async with event_lock(event.id):
        updated = await EventRepository(bot.db).update(event.id, **values) if values else event
        assert updated is not None
        if capacity_changed:
            promoted, demoted = await ParticipantRepository(bot.db).rebalance(event.id, updated.max_participants)
    if new_mode is not None:
        await InhouseRepository(bot.db).update_mode(event.id, new_mode)
        if teams_exist:
            await _drop_teams(bot, session)
            changes.append("🧹 Les équipes précédentes ont été supprimées : regénère-les avec `/inhouse equipes-generer`.")
    if date_changed:
        await ReminderRepository(bot.db).reset(event.id)
    log.info("Inhouse %s modifié par %s (%s) : %s", event.id, user, user.id, [*values, *(["mode"] if new_mode else [])])

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
    embed.set_footer(text=f"Inhouse #{updated.id}")
    view = None
    if url := event_message_url(updated):
        view = discord.ui.View()
        view.add_item(link_button(url))
    return embed, view


@inhouse_group.command(name="modifier", description="Modifier une session d'inhouse (mode, date, places, titre…)")
@app_commands.describe(
    session="La session d'inhouse (tape pour chercher)",
    mode="Nouveau mode de jeu (supprime les équipes déjà générées)",
    date="Nouvelle date locale : 28/09 21h, demain 20h30, samedi 18h…",
    places="Nouveau nombre de places (0 = illimité)",
    titre="Nouveau titre",
    description="Nouvelle description (« - » pour l'effacer)",
)
@app_commands.choices(mode=MODE_CHOICES)
@app_commands.autocomplete(session=inhouse_autocomplete)
@organizer_only()
async def edit_inhouse(
    interaction: discord.Interaction,
    session: int,
    mode: Optional[app_commands.Choice[str]] = None,
    date: Optional[str] = None,
    places: Optional[app_commands.Range[int, 0, 200]] = None,
    titre: Optional[app_commands.Range[str, 2, 100]] = None,
    description: Optional[app_commands.Range[str, 1, 2000]] = None,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event, ih = await resolve_inhouse(interaction, session)
    if not event.is_active:
        raise UserFacingError(f"**{event.title}** est terminé : il ne peut plus être modifié.")
    if all(v is None for v in (mode, date, places, titre, description)):
        raise UserFacingError(
            "Tu n'as rien indiqué à modifier ! Renseigne au moins une option : "
            "`mode`, `date`, `places`, `titre` ou `description`."
        )

    old_mode = get_mode(ih.game_mode)
    new_mode = get_mode(mode.value) if mode is not None and mode.value != ih.game_mode else None
    target_mode = new_mode or old_mode
    values: dict = {}
    changes: list[str] = []

    if new_mode is not None:
        changes.append(f"**Mode** : {old_mode.display} → {new_mode.display}")
    if titre is not None and titre.strip() != event.title:
        values["title"] = titre.strip()
        changes.append(f"**Titre** : {event.title} → {values['title']}")
    new_start = parse_event_date(bot, date) if date is not None else None
    date_changed = new_start is not None and new_start != event.starts_at
    if date_changed:
        values["starts_at"] = new_start
        changes.append(f"**Date** : {discord_full(event.starts_at)} → {discord_full(new_start)}")  # type: ignore[arg-type]
        if event.status == STATUS_ONGOING:
            values["status"] = STATUS_SCHEDULED
            values["registration_open"] = True
            changes.append("**État** : en cours → programmé (inscriptions rouvertes)")
    if description is not None:
        new_desc = None if description.strip() == CLEAR else description.strip()
        if new_desc != event.description:
            values["description"] = new_desc
            changes.append("**Description** : " + ("effacée" if new_desc is None else "mise à jour"))

    new_max: int | None = event.max_participants
    if places is not None:
        new_max = None if places == 0 else places
    elif new_mode is not None and event.max_participants == old_mode.default_max:
        new_max = new_mode.default_max  # la capacité par défaut suit le mode
    tip = check_capacity(target_mode, new_max) if (places is not None or new_mode is not None) else None
    capacity_changed = new_max != event.max_participants
    if capacity_changed:
        values["max_participants"] = new_max
        changes.append(f"**Places** : {event.max_participants or 'illimité'} → {new_max or 'illimité'}")
    if tip:
        changes.append(tip)

    if not values and new_mode is None:
        await embeds.reply(interaction, embeds.info("Ces valeurs sont déjà celles de la session : rien à changer 🙂"))
        return

    teams_exist = await InhouseTeamRepository(bot.db).has_teams(event.id)
    kwargs = dict(
        new_mode=new_mode.key if new_mode else None,
        capacity_changed=capacity_changed,
        date_changed=date_changed,
        teams_exist=teams_exist,
    )

    if new_mode is not None and teams_exist:
        async def on_confirm(button_interaction: discord.Interaction) -> None:
            await button_interaction.response.defer()
            embed, view = await _apply(bot, interaction.user, event, ih, values, changes, **kwargs)
            await button_interaction.edit_original_response(embed=embed, view=view)

        warn = embeds.warning(
            f"Passer **{event.title}** en {new_mode.display} va **supprimer les équipes déjà générées**"
            + (" et leur message public" if ih.teams_published else "")
            + ". Les inscriptions sont conservées.\nContinuer ?",
            title="Changer de mode ?",
        )
        confirm = ConfirmView(interaction.user.id, on_confirm, confirm_label="Oui, changer de mode",
                              cancel_text="Modification annulée, rien n'a changé.")
        await interaction.response.send_message(embed=warn, view=confirm, ephemeral=True)
        confirm.message = await interaction.original_response()
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    embed, view = await _apply(bot, interaction.user, event, ih, values, changes, **kwargs)
    await embeds.reply(interaction, embed, ephemeral=True, view=view)
