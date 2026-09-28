"""``/inhouse inscrits`` : liste détaillée des inscrits (ouverte à tous, réponse éphémère).

Pour chaque inscrit : Riot ID, rang, rôles ; puis liste d'attente, répartition des rôles
principaux (Faille), niveau moyen, liens MultiGG et avertissements (compte non lié…).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.features.inhouse.autocomplete import inhouse_autocomplete, resolve_inhouse
from bot.features.inhouse.constants import get_mode
from bot.features.inhouse.group import inhouse_group
from bot.features.inhouse.players import (
    card_of,
    load_players,
    main_role_counts,
    missing_roles_text,
    multigg_links,
    player_line,
    role_summary,
    score_label,
)
from bot.repositories.participants import REGISTERED, WAITLIST, ParticipantRepository
from bot.utils import embeds
from bot.utils.embeds import Colors, Emojis, chunk_lines, fit_embed
from bot.utils.time import discord_full

if TYPE_CHECKING:
    from bot.core.bot import STFBot
    from bot.repositories.events import Event

MAX_FIELDS_REGISTERED = 8
MAX_FIELDS_WAITLIST = 3


@inhouse_group.command(name="inscrits", description="Voir le détail des inscrits d'un inhouse (rangs, rôles, MultiGG)")
@app_commands.describe(session="La session d'inhouse (tape pour chercher)")
@app_commands.autocomplete(session=inhouse_autocomplete)
async def roster_command(interaction: discord.Interaction, session: int) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event, ih = await resolve_inhouse(interaction, session)
    embed = await build_roster_embed(bot, event, ih.game_mode)
    await embeds.reply(interaction, embed, ephemeral=True)


async def build_roster_embed(bot: "STFBot", event: "Event", game_mode: str) -> discord.Embed:
    """Embed détaillé des inscrits (aussi utilisé par le bouton générique « Voir les inscrits »)."""
    mode = get_mode(game_mode)
    participants = await ParticipantRepository(bot.db).list(event.id)
    registered = [p.discord_id for p in participants if p.status == REGISTERED]
    waiting = [p.discord_id for p in participants if p.status == WAITLIST]
    cards = await load_players(bot, [*registered, *waiting])

    places = (
        f"{len(registered)}/{event.max_participants}" if event.max_participants else f"{len(registered)} (illimité)"
    )
    state = f"{Emojis.UNLOCK} ouvertes" if event.registration_open and event.is_active else f"{Emojis.LOCK} fermées"
    embed = discord.Embed(
        title=f"{Emojis.PEOPLE} Inscrits · ⚔️ {event.title}"[:256],
        description=(
            f"{mode.display} · {mode.format_text}\n"
            f"{Emojis.CALENDAR} {discord_full(event.starts_at)}\n"
            f"Places : **{places}** · Inscriptions {state}"
        ),
        color=Colors.INHOUSE,
    )

    if registered:
        lines = [player_line(card_of(cards, uid), index=i) for i, uid in enumerate(registered, start=1)]
        chunks = chunk_lines(lines)
        for idx, chunk in enumerate(chunks[:MAX_FIELDS_REGISTERED]):
            embed.add_field(
                name=f"{Emojis.SUCCESS} Inscrits ({len(registered)})" if idx == 0 else "​", value=chunk, inline=False
            )
    else:
        embed.add_field(name=f"{Emojis.SUCCESS} Inscrits (0)", value="*Personne pour l'instant.*", inline=False)

    if waiting:
        lines = [player_line(card_of(cards, uid), index=i) for i, uid in enumerate(waiting, start=1)]
        for idx, chunk in enumerate(chunk_lines(lines)[:MAX_FIELDS_WAITLIST]):
            embed.add_field(name=f"⏳ Liste d'attente ({len(waiting)})" if idx == 0 else "​", value=chunk, inline=False)

    if registered:
        reg_cards = [card_of(cards, uid) for uid in registered]
        if mode.uses_roles:
            counts = main_role_counts(reg_cards)
            needed = max(2, (len(registered) // mode.players_per_match) * 2)
            value = role_summary(counts)
            value += f"\n{missing_roles_text(counts, needed) or '✅ Tous les rôles sont couverts !'}"
            embed.add_field(name="🧩 Rôles principaux", value=value[:1024], inline=False)

        known = [c.rank_score for c in reg_cards if c.rank_score is not None]
        if known:
            avg = sum(known) / len(known)
            embed.add_field(
                name=f"{Emojis.CHART} Niveau",
                value=f"Moyenne : **{score_label(avg)}** · {len(known)}/{len(registered)} rang(s) connu(s)",
                inline=False,
            )
        if links := multigg_links(bot, cards, registered):
            embed.add_field(name=f"{Emojis.LINK} MultiGG", value=links[:1024], inline=False)

        warnings = []
        unlinked = [c for c in reg_cards if not c.linked]
        if unlinked:
            warnings.append(
                f"{len(unlinked)} inscrit(s) sans compte LoL lié : "
                + ", ".join(f"<@{c.discord_id}>" for c in unlinked[:15])
                + " → bouton **🔗 Lier mon compte LoL** ou `/compte lier`."
            )
        if mode.uses_roles:
            no_roles = [c for c in reg_cards if not c.roles]
            if no_roles:
                warnings.append(
                    f"{len(no_roles)} inscrit(s) sans rôle : "
                    + ", ".join(f"<@{c.discord_id}>" for c in no_roles[:15])
                    + " → bouton **🎮 Choisir mes rôles** ou `/compte roles`."
                )
        if len(registered) < mode.min_players:
            warnings.append(
                f"Encore **{mode.min_players - len(registered)}** joueur(s) avant de pouvoir lancer une partie."
            )
        if warnings:
            embed.add_field(name=f"{Emojis.WARNING} À noter", value="\n".join(f"• {w}" for w in warnings)[:1024], inline=False)

    embed.set_footer(text=f"Inhouse #{event.id} · Visible uniquement par toi")
    return fit_embed(embed)
