"""Inscriptions / désinscriptions aux événements (logique partagée par les boutons et les commandes).

Deux niveaux :

- **Cœur sans Discord** (testable) : ``check_joinable``, ``register_participant``,
  ``unregister_participant``, ``waitlist_position`` — protégés par un verrou asyncio par
  événement pour qu'un rush de clics ne dépasse jamais la capacité.
- **Niveau Discord** : ``join_event`` / ``leave_event`` (réponse éphémère sympa, hooks du type
  d'événement, notification des promus, mise à jour de l'annonce) et ``remove_participant``
  (utilisé aussi par ``/evenement retirer``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import discord

from bot.core.errors import NotFoundError, UserFacingError
from bot.db import Database
from bot.features.events.announcement import capacity_label, event_message_url, refresh_event_message
from bot.features.events.kinds import get_kind
from bot.features.events.notifications import notify_promoted
from bot.repositories.events import STATUS_FINISHED, Event, EventRepository
from bot.repositories.participants import REGISTERED, WAITLIST, Participant, ParticipantRepository
from bot.repositories.users import UserRepository
from bot.utils import embeds
from bot.utils.embeds import Colors
from bot.utils.time import discord_full, now_utc

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

_locks: dict[int, asyncio.Lock] = {}


def event_lock(event_id: int) -> asyncio.Lock:
    """Verrou propre à un événement : toute modification des inscrits passe par lui."""
    lock = _locks.get(event_id)
    if lock is None:
        lock = _locks[event_id] = asyncio.Lock()
    return lock


# ====================================================================== cœur (sans Discord)
@dataclass(slots=True)
class JoinResult:
    event: Event
    participant: Participant
    already: bool                 # déjà inscrit (ou déjà en attente) avant ce clic
    position: int | None = None   # rang dans la liste d'attente (None si inscrit)
    registered_count: int = 0

    @property
    def on_waitlist(self) -> bool:
        return self.participant.status == WAITLIST


@dataclass(slots=True)
class LeaveResult:
    event: Event
    removed: Participant | None                        # None = n'était pas inscrit
    promoted: list[int] = field(default_factory=list)  # membres sortis de la liste d'attente


def check_joinable(event: Event | None, *, now: datetime | None = None) -> Event:
    """Vérifie qu'on peut s'inscrire à cet événement, sinon lève une erreur qui explique pourquoi."""
    if event is None:
        raise NotFoundError("Cet événement n'existe plus (il a peut-être été supprimé).")
    if not event.is_active:
        what = "terminé" if event.status == STATUS_FINISHED else "annulé"
        raise UserFacingError(f"**{event.title}** est {what} : les inscriptions ne sont plus possibles.")
    if event.starts_at <= (now or now_utc()):
        raise UserFacingError(
            f"**{event.title}** a déjà commencé, trop tard pour s'inscrire ! "
            "Contacte un organisateur si tu veux quand même participer."
        )
    if not event.registration_open:
        raise UserFacingError(
            f"Les inscriptions à **{event.title}** sont fermées pour le moment 🔒. "
            "Garde un œil sur l'annonce ou demande à un organisateur."
        )
    return event


async def waitlist_position(db: Database, event_id: int, discord_id: int) -> int | None:
    waiting = await ParticipantRepository(db).list(event_id, status=WAITLIST)
    for i, p in enumerate(waiting, start=1):
        if p.discord_id == discord_id:
            return i
    return None


async def register_participant(db: Database, event_id: int, discord_id: int) -> JoinResult:
    """Inscrit le membre (ou le met en liste d'attente si complet) de façon atomique."""
    async with event_lock(event_id):
        event = check_joinable(await EventRepository(db).get(event_id))
        repo = ParticipantRepository(db)
        existing = await repo.get(event_id, discord_id)
        participant = existing or await repo.add(event_id, discord_id, max_participants=event.max_participants)
        position = await waitlist_position(db, event_id, discord_id) if participant.on_waitlist else None
        return JoinResult(
            event=event,
            participant=participant,
            already=existing is not None,
            position=position,
            registered_count=await repo.count(event_id),
        )


async def unregister_participant(db: Database, event_id: int, discord_id: int) -> LeaveResult:
    """Désinscrit le membre et fait monter la liste d'attente si une place se libère."""
    async with event_lock(event_id):
        event = await EventRepository(db).get(event_id)
        if event is None:
            raise NotFoundError("Cet événement n'existe plus (il a peut-être été supprimé).")
        repo = ParticipantRepository(db)
        existing = await repo.get(event_id, discord_id)
        if existing is None:
            return LeaveResult(event=event, removed=None)
        await repo.remove(event_id, discord_id)
        promoted: list[int] = []
        if existing.status == REGISTERED and event.is_active:
            promoted = await repo.promote_waitlist(event_id, event.max_participants)
        return LeaveResult(event=event, removed=existing, promoted=promoted)


# ====================================================================== niveau Discord
async def _get_guild_event(bot: "STFBot", interaction: discord.Interaction, event_id: int) -> Event:
    event = await EventRepository(bot.db).get(event_id)
    if event is None or (interaction.guild_id is not None and event.guild_id != interaction.guild_id):
        raise NotFoundError(
            "Cet événement n'existe plus (il a peut-être été supprimé). "
            "Tape `/evenement liste` pour voir les événements à venir."
        )
    return event


async def join_event(bot: "STFBot", interaction: discord.Interaction, event_id: int) -> None:
    """Inscrit l'auteur de l'interaction et lui répond en éphémère.

    L'interaction n'est pas différée avant ``kind.check_can_join`` : un type d'événement peut
    donc encore y répondre (ex. ouvrir un sélecteur de rôles) ; s'il fait un appel lent, c'est
    à lui de différer (``embeds.reply`` gère les deux cas).
    """
    user = interaction.user
    event = await _get_guild_event(bot, interaction, event_id)
    kind = get_kind(event.type)
    participants = ParticipantRepository(bot.db)

    existing = await participants.get(event.id, user.id)
    if existing is not None:
        if existing.on_waitlist:
            pos = await waitlist_position(bot.db, event.id, user.id)
            msg = (
                f"Tu es déjà en **liste d'attente** pour **{event.title}** (n°{pos}). "
                "Je te préviens dès qu'une place se libère 😉"
            )
        else:
            msg = f"Tu es déjà inscrit·e à **{event.title}** 😉 Rendez-vous {discord_full(event.starts_at)} !"
        await embeds.reply(interaction, embeds.info(msg, title="Déjà inscrit·e"), ephemeral=True)
        return

    check_joinable(event)
    await kind.check_can_join(bot, interaction, event)

    await UserRepository(bot.db).ensure(user.id, getattr(user, "display_name", None))
    result = await register_participant(bot.db, event.id, user.id)
    event = result.event

    extra: str | None = None
    try:
        extra = await kind.on_joined(bot, interaction, event, result.participant)
    except Exception:  # noqa: BLE001 - l'inscription est faite, on ne la gâche pas pour un hook
        log.exception("kind.on_joined a échoué (événement %s, membre %s)", event.id, user.id)

    if result.on_waitlist:
        log.info("%s (%s) en liste d'attente de l'événement %s (n°%s)", user, user.id, event.id, result.position)
        embed = discord.Embed(
            title="⏳ C'est complet… mais tu es dans la file !",
            description=(
                f"**{event.title}** affiche complet ({capacity_label(event, result.registered_count)}).\n"
                f"Tu es **n°{result.position}** en liste d'attente : je te préviens en MP "
                "dès qu'une place se libère."
            ),
            color=Colors.WARNING,
        )
    else:
        log.info("%s (%s) inscrit à l'événement %s", user, user.id, event.id)
        embed = discord.Embed(
            title="✅ Inscription confirmée !",
            description=(
                f"Tu es inscrit·e à **{event.title}** 🎉\n"
                f"📅 {discord_full(event.starts_at)}\n"
                f"👥 Places : **{capacity_label(event, result.registered_count)}**"
            ),
            color=Colors.SUCCESS,
        )
        if interaction.guild_id is not None:
            settings = await bot.settings.get(interaction.guild_id)
            if settings.reminder_offsets:
                embed.description += "\n⏰ Je posterai un rappel avant le début."  # type: ignore[operator]
    if extra:
        embed.add_field(name=f"{kind.emoji} {kind.label}", value=extra[:1024], inline=False)
    embed.set_footer(text="Un empêchement ? Clique sur « Quitter » ou tape /evenement quitter.")
    await embeds.reply(interaction, embed, ephemeral=True)
    await refresh_event_message(bot, event.id)


async def remove_participant(bot: "STFBot", event: Event, discord_id: int) -> LeaveResult:
    """Retire un membre (désinscription ou retrait par un organisateur) + effets de bord.

    Appelle ``kind.on_left``, prévient les promus de la liste d'attente et met à jour l'annonce.
    """
    result = await unregister_participant(bot.db, event.id, discord_id)
    if result.removed is None:
        return result
    log.info("Membre %s retiré de l'événement %s (promus : %s)", discord_id, event.id, result.promoted)
    try:
        await get_kind(result.event.type).on_left(bot, result.event, discord_id)
    except Exception:  # noqa: BLE001
        log.exception("kind.on_left a échoué (événement %s, membre %s)", event.id, discord_id)
    await refresh_event_message(bot, event.id)
    if result.promoted:
        await notify_promoted(bot, result.event, result.promoted)
    return result


async def leave_event(bot: "STFBot", interaction: discord.Interaction, event_id: int) -> None:
    """Désinscrit l'auteur de l'interaction et lui répond en éphémère."""
    user = interaction.user
    event = await _get_guild_event(bot, interaction, event_id)
    if not event.is_active:
        raise UserFacingError(f"**{event.title}** est terminé : plus besoin de te désinscrire 😉")

    if await ParticipantRepository(bot.db).get(event.id, user.id) is None:
        await embeds.reply(
            interaction,
            embeds.info(
                f"Tu n'étais pas inscrit·e à **{event.title}**. "
                "Clique sur « Rejoindre » si tu veux participer !",
                title="Pas inscrit·e",
            ),
            ephemeral=True,
        )
        return

    # On répond d'abord (les MP aux promus peuvent prendre un peu de temps).
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    result = await remove_participant(bot, event, user.id)
    if result.removed is None:  # retiré entre-temps (double clic)
        await embeds.reply(interaction, embeds.info(f"Tu n'es plus inscrit·e à **{event.title}**."))
        return

    was_waiting = result.removed.on_waitlist
    text = (
        f"Tu as quitté la liste d'attente de **{event.title}**."
        if was_waiting
        else f"Tu es désinscrit·e de **{event.title}**. Dommage, à la prochaine ! 👋"
    )
    if result.promoted:
        text += "\nTa place a été attribuée au premier de la liste d'attente."
    if url := event_message_url(event):
        text += f"\nChangé d'avis ? [Réinscris-toi depuis l'annonce]({url})."
    await embeds.reply(interaction, embeds.success(text, title="Désinscription"), ephemeral=True)
