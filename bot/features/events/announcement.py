"""Message d'annonce d'un événement : embed, boutons, publication et mise à jour en direct.

API publique (utilisée par les autres fichiers du paquet et par les types d'événements) :

- ``build_event_embed(bot, event)``        -> embed de l'annonce (hook ``kind.build_embed``) ;
- ``build_event_view(bot, event)``         -> boutons persistants (+ ``kind.extra_components``) ;
- ``publish_event_message(bot, event, channel)`` -> publie et mémorise le message ;
- ``refresh_event_message(bot, event_id)`` -> met à jour le message publié (ne lève jamais) ;
- ``build_participants_embed(bot, event)`` -> liste détaillée des inscrits ;
- ``get_announcement_channel(bot, event)`` / ``event_message_url(event)``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from bot.core.errors import UserFacingError
from bot.features.events.kinds import get_kind
from bot.repositories.events import (
    STATUS_CANCELLED,
    STATUS_FINISHED,
    STATUS_ONGOING,
    Event,
    EventRepository,
)
from bot.repositories.participants import REGISTERED, WAITLIST, Participant, ParticipantRepository
from bot.utils.embeds import Colors, Emojis, chunk_lines, fit_embed
from bot.utils.time import discord_full, discord_ts

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

# Nombre maximum de mentions affichées dans l'annonce (le reste : « … et N autres »).
MAX_LISTED = 40


# ---------------------------------------------------------------------- petits helpers
def status_label(event: Event) -> str:
    return {
        STATUS_ONGOING: "🟢 En cours",
        STATUS_FINISHED: "🏁 Terminé",
        STATUS_CANCELLED: "❌ Annulé",
    }.get(event.status, "🗓️ Programmé")


def registration_label(event: Event) -> str:
    if event.registration_open and event.is_active and not event.has_started:
        return f"{Emojis.UNLOCK} Ouvertes"
    return f"{Emojis.LOCK} Fermées"


def capacity_label(event: Event, registered: int) -> str:
    """« 7/10 » ou « 7 · illimité »."""
    if event.max_participants is None:
        return f"{registered} · illimité"
    full = " (complet)" if registered >= event.max_participants else ""
    return f"{registered}/{event.max_participants}{full}"


def event_message_url(event: Event) -> str | None:
    """Lien vers le message d'annonce (``None`` s'il n'a pas été publié)."""
    if event.channel_id is None or event.message_id is None:
        return None
    return f"https://discord.com/channels/{event.guild_id}/{event.channel_id}/{event.message_id}"


def event_title_link(event: Event) -> str:
    """Titre en gras, cliquable vers l'annonce si elle existe."""
    url = event_message_url(event)
    return f"**[{event.title}]({url})**" if url else f"**{event.title}**"


def mention_list(user_ids: list[int], *, numbered: bool = True, limit: int = MAX_LISTED, max_chars: int = 1024) -> str:
    """Liste de mentions qui tient dans un champ d'embed."""
    if not user_ids:
        return "*Personne pour l'instant.*"
    lines: list[str] = []
    used = 0
    for i, uid in enumerate(user_ids, start=1):
        line = f"`{i:>2}.` <@{uid}>" if numbered else f"<@{uid}>"
        if i > limit or used + len(line) + 1 > max_chars - 25:  # 25 = place pour « … et N autres »
            rest = len(user_ids) - len(lines)
            lines.append(f"… et {rest} autre{'s' if rest > 1 else ''}")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def link_button(url: str, label: str = "Voir l'annonce") -> discord.ui.Button:
    return discord.ui.Button(style=discord.ButtonStyle.link, url=url, label=label, emoji=Emojis.LINK)


# ---------------------------------------------------------------------- embeds
async def build_event_embed(bot: "STFBot", event: Event) -> discord.Embed:
    """Embed de l'annonce : celui du type d'événement s'il en fournit un, sinon l'embed générique."""
    participants = await ParticipantRepository(bot.db).list(event.id)
    kind = get_kind(event.type)
    try:
        custom = await kind.build_embed(bot, event, participants)
    except Exception:  # noqa: BLE001 - un type défaillant ne doit pas casser l'annonce
        log.exception("kind.build_embed a échoué pour l'événement %s (%s)", event.id, event.type)
        custom = None
    if custom is not None:
        return fit_embed(custom)
    return fit_embed(generic_event_embed(event, participants))


def generic_event_embed(event: Event, participants: list[Participant]) -> discord.Embed:
    kind = get_kind(event.type)
    registered = [p.discord_id for p in participants if p.status == REGISTERED]
    waiting = [p.discord_id for p in participants if p.status == WAITLIST]

    color = kind.color if event.is_active else Colors.NEUTRAL
    embed = discord.Embed(
        title=f"{kind.emoji} {event.title}"[:256],
        description=(event.description or "")[:4000] or None,
        color=color,
        timestamp=event.starts_at,
    )
    embed.add_field(name=f"{Emojis.CALENDAR} Date", value=discord_full(event.starts_at), inline=False)
    embed.add_field(name=f"{Emojis.PEOPLE} Places", value=capacity_label(event, len(registered)), inline=True)
    embed.add_field(name="📝 Inscriptions", value=registration_label(event), inline=True)
    if event.status != "scheduled":
        embed.add_field(name="État", value=status_label(event), inline=True)
    embed.add_field(name="🎤 Organisé par", value=f"<@{event.created_by}>", inline=True)

    embed.add_field(
        name=f"{Emojis.SUCCESS} Inscrits ({len(registered)})",
        value=mention_list(registered),
        inline=False,
    )
    if waiting:
        embed.add_field(
            name=f"⏳ Liste d'attente ({len(waiting)})",
            value=mention_list(waiting, limit=15),
            inline=False,
        )

    footer = f"Événement #{event.id}"
    if kind.key != "generic":
        footer += f" · {kind.label}"
    if event.is_active and event.registration_open and not event.has_started:
        footer += " · Clique sur « Rejoindre » pour t'inscrire !"
    embed.set_footer(text=footer)
    return embed


async def build_participants_embed(bot: "STFBot", event: Event) -> discord.Embed:
    """Liste détaillée (éphémère) des inscrits et de la liste d'attente."""
    kind = get_kind(event.type)
    try:
        custom = await kind.build_participants_embed(bot, event)
    except Exception:  # noqa: BLE001 - on retombe sur l'affichage générique
        log.exception("build_participants_embed a échoué pour l'événement #%s", event.id)
        custom = None
    if custom is not None:
        return fit_embed(custom)
    participants = await ParticipantRepository(bot.db).list(event.id)
    registered = [p for p in participants if p.status == REGISTERED]
    waiting = [p for p in participants if p.status == WAITLIST]

    embed = discord.Embed(
        title=f"{Emojis.PEOPLE} Inscrits · {kind.emoji} {event.title}"[:256],
        description=(
            f"{Emojis.CALENDAR} {discord_full(event.starts_at)}\n"
            f"Places : **{capacity_label(event, len(registered))}** · Inscriptions {registration_label(event)}"
        ),
        color=kind.color,
    )

    def lines_for(items: list[Participant]) -> list[str]:
        return [
            f"`{i:>2}.` <@{p.discord_id}> · {discord_ts(p.joined_at, 'R')}" for i, p in enumerate(items, start=1)
        ]

    if registered:
        for idx, chunk in enumerate(chunk_lines(lines_for(registered))[:4]):
            embed.add_field(
                name=f"{Emojis.SUCCESS} Inscrits ({len(registered)})" if idx == 0 else "​",
                value=chunk,
                inline=False,
            )
    else:
        embed.add_field(
            name=f"{Emojis.SUCCESS} Inscrits (0)",
            value="*Personne pour l'instant… sois le ou la premier·e !*",
            inline=False,
        )
    if waiting:
        for idx, chunk in enumerate(chunk_lines(lines_for(waiting))[:2]):
            embed.add_field(
                name=f"⏳ Liste d'attente ({len(waiting)})" if idx == 0 else "​",
                value=chunk,
                inline=False,
            )
    embed.set_footer(text=f"Événement #{event.id}")
    return fit_embed(embed)


# ---------------------------------------------------------------------- boutons
def can_join_now(event: Event) -> bool:
    return event.is_active and event.registration_open and not event.has_started


async def build_event_view(bot: "STFBot", event: Event) -> discord.ui.View:
    """Boutons persistants de l'annonce (Rejoindre / Quitter / Voir les inscrits + extras du type)."""
    # Import local : buttons -> registration_service -> announcement (évite l'import circulaire).
    from bot.features.events.buttons import JoinButton, LeaveButton, ParticipantsButton

    view = discord.ui.View(timeout=None)
    view.add_item(JoinButton(event.id, disabled=not can_join_now(event)))
    view.add_item(LeaveButton(event.id, disabled=not event.is_active))
    view.add_item(ParticipantsButton(event.id))
    try:
        for item in await get_kind(event.type).extra_components(bot, event):
            view.add_item(item)
    except Exception:  # noqa: BLE001
        log.exception("kind.extra_components a échoué pour l'événement %s", event.id)
    return view


# ---------------------------------------------------------------------- publication
async def get_announcement_channel(bot: "STFBot", event: Event) -> discord.abc.Messageable | None:
    """Salon où l'annonce a été publiée (``None`` si inconnu ou inaccessible)."""
    if event.channel_id is None:
        return None
    channel = bot.get_channel(event.channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(event.channel_id)
        except discord.HTTPException:
            return None
    return channel if isinstance(channel, discord.abc.Messageable) else None


def ensure_can_post(channel: discord.abc.GuildChannel | discord.Thread, me: discord.Member) -> None:
    """Lève une erreur claire si le bot ne peut pas publier d'embed dans ce salon."""
    perms = channel.permissions_for(me)
    missing = []
    if not perms.view_channel:
        missing.append("Voir le salon")
    if not (perms.send_messages_in_threads if isinstance(channel, discord.Thread) else perms.send_messages):
        missing.append("Envoyer des messages")
    if not perms.embed_links:
        missing.append("Intégrer des liens")
    if missing:
        raise UserFacingError(
            f"Je ne peux pas publier dans {channel.mention} : il me manque "
            + ", ".join(f"**{m}**" for m in missing)
            + ". Donne-moi ces permissions ou choisis un autre salon."
        )


async def publish_event_message(
    bot: "STFBot",
    event: Event,
    channel: discord.abc.Messageable,
    *,
    content: str | None = None,
    allowed_mentions: discord.AllowedMentions | None = None,
) -> discord.Message:
    """Publie l'annonce (embed + boutons) et la mémorise comme message principal de l'événement."""
    embed = await build_event_embed(bot, event)
    view = await build_event_view(bot, event)
    kwargs: dict = {"embed": embed, "view": view}
    if content:
        kwargs["content"] = content
        kwargs["allowed_mentions"] = allowed_mentions or discord.AllowedMentions.none()
    message = await channel.send(**kwargs)
    await EventRepository(bot.db).update(event.id, channel_id=message.channel.id, message_id=message.id)
    log.info("Annonce de l'événement %s publiée (salon %s, message %s)", event.id, message.channel.id, message.id)
    return message


async def refresh_event_message(bot: "STFBot", event_id: int) -> None:
    """Met à jour le message d'annonce. Ne lève jamais d'exception.

    Si le message a été supprimé ou n'est plus accessible, on l'oublie (``message_id = NULL``).
    """
    try:
        repo = EventRepository(bot.db)
        event = await repo.get(event_id)
        if event is None or event.channel_id is None or event.message_id is None:
            return
        channel = await get_announcement_channel(bot, event)
        if channel is None or not hasattr(channel, "get_partial_message"):
            log.warning("Salon %s de l'événement %s introuvable : annonce oubliée", event.channel_id, event_id)
            await repo.update(event_id, message_id=None)
            return
        embed = await build_event_embed(bot, event)
        view = await build_event_view(bot, event)
        try:
            await channel.get_partial_message(event.message_id).edit(embed=embed, view=view)  # type: ignore[attr-defined]
        except (discord.NotFound, discord.Forbidden) as exc:
            log.warning(
                "Annonce de l'événement %s inaccessible (%s) : message oublié", event_id, type(exc).__name__
            )
            await repo.update(event_id, message_id=None)
    except Exception:  # noqa: BLE001 - la mise à jour d'une annonce ne doit jamais faire échouer l'action
        log.exception("Impossible de mettre à jour l'annonce de l'événement %s", event_id)


async def delete_event_message(bot: "STFBot", event: Event) -> bool:
    """Supprime le message d'annonce principal (renvoie ``True`` si supprimé). Ne lève jamais."""
    if event.channel_id is None or event.message_id is None:
        return False
    try:
        channel = await get_announcement_channel(bot, event)
        if channel is None or not hasattr(channel, "get_partial_message"):
            return False
        await channel.get_partial_message(event.message_id).delete()  # type: ignore[attr-defined]
        return True
    except discord.HTTPException as exc:
        log.info("Annonce de l'événement %s non supprimée (%s)", event.id, exc)
        return False
    except Exception:  # noqa: BLE001
        log.exception("Erreur en supprimant l'annonce de l'événement %s", event.id)
        return False


async def resolve_target_channel(
    bot: "STFBot", interaction: discord.Interaction, explicit: discord.abc.GuildChannel | discord.Thread | None
) -> discord.abc.GuildChannel | discord.Thread:
    """Salon où publier : celui choisi, sinon le salon d'annonces configuré, sinon le salon courant."""
    guild = interaction.guild
    if guild is None:
        raise UserFacingError("Cette commande ne fonctionne que sur un serveur.")
    channel = explicit
    if channel is None:
        settings = await bot.settings.get(guild.id)
        if settings.announce_channel_id:
            channel = guild.get_channel_or_thread(settings.announce_channel_id)
    if channel is None:
        channel = interaction.channel  # type: ignore[assignment]
    if channel is None or not isinstance(channel, discord.abc.Messageable):
        raise UserFacingError("Je ne sais pas où publier l'annonce : précise l'option `salon`.")
    ensure_can_post(channel, guild.me)
    return channel
