"""Prévenir des membres à propos d'un événement (promotion de la liste d'attente, retrait…).

Stratégie : message privé d'abord ; si les MP sont fermés, une mention groupée dans le
salon de l'annonce (pour que personne ne rate l'info).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from bot.features.events.announcement import event_message_url, get_announcement_channel
from bot.features.events.kinds import get_kind
from bot.repositories.events import Event
from bot.utils.embeds import Colors
from bot.utils.time import discord_full

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


def _event_embed(event: Event, title: str, text: str, color: discord.Color) -> discord.Embed:
    kind = get_kind(event.type)
    embed = discord.Embed(title=title[:256], description=text, color=color)
    embed.add_field(name=f"{kind.emoji} Événement", value=f"**{event.title}**", inline=True)
    embed.add_field(name="📅 Date", value=discord_full(event.starts_at), inline=True)
    if url := event_message_url(event):
        embed.add_field(name="🔗 Annonce", value=f"[Voir l'annonce]({url})", inline=False)
    embed.set_footer(text=f"Événement #{event.id}")
    return embed


async def notify_members(
    bot: "STFBot",
    event: Event,
    user_ids: list[int],
    *,
    title: str,
    text: str,
    color: discord.Color = Colors.INFO,
    fallback_text: str | None = None,
) -> None:
    """Envoie un MP à chaque membre ; ceux injoignables sont mentionnés dans le salon de l'annonce.

    Ne lève jamais d'exception.
    """
    if not user_ids:
        return
    unreachable: list[int] = []
    embed = _event_embed(event, title, text, color)
    for uid in user_ids:
        try:
            user = bot.get_user(uid) or await bot.fetch_user(uid)
            await user.send(embed=embed)
        except discord.HTTPException:
            unreachable.append(uid)
        except Exception:  # noqa: BLE001
            log.exception("MP impossible à %s pour l'événement %s", uid, event.id)
            unreachable.append(uid)

    if not unreachable:
        return
    try:
        channel = await get_announcement_channel(bot, event)
        if channel is None:
            log.info("Impossible de prévenir %s (MP fermés, pas de salon d'annonce)", unreachable)
            return
        mentions = " ".join(f"<@{uid}>" for uid in unreachable[:40])
        await channel.send(
            f"{mentions} {fallback_text or text}"[:2000],
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
    except Exception:  # noqa: BLE001
        log.exception("Impossible de prévenir %s dans le salon de l'événement %s", unreachable, event.id)


async def notify_promoted(bot: "STFBot", event: Event, user_ids: list[int]) -> None:
    await notify_members(
        bot,
        event,
        user_ids,
        title="🎉 Une place s'est libérée pour toi !",
        text=(
            f"Bonne nouvelle : tu passes de la liste d'attente aux **inscrits** de **{event.title}**. "
            "Si tu ne peux plus venir, désinscris-toi pour laisser ta place 🙏"
        ),
        color=Colors.SUCCESS,
        fallback_text=f"🎉 une place s'est libérée : vous êtes maintenant inscrit·e·s à **{event.title}** !",
    )


async def notify_demoted(bot: "STFBot", event: Event, user_ids: list[int]) -> None:
    await notify_members(
        bot,
        event,
        user_ids,
        title="⏳ Tu passes en liste d'attente",
        text=(
            f"Le nombre de places de **{event.title}** a été réduit par un organisateur : "
            "tu es désormais en **liste d'attente**. Je te préviendrai si une place se libère."
        ),
        color=Colors.WARNING,
        fallback_text=f"⏳ le nombre de places de **{event.title}** a baissé : vous passez en liste d'attente.",
    )
