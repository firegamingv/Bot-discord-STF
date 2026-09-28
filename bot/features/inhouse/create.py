"""``/inhouse creer`` : créer une session d'inhouse et publier son annonce.

Une session = un événement générique de type ``inhouse`` (inscriptions, liste d'attente,
rappels automatiques…) + une ligne ``inhouse_sessions`` (mode de jeu, équipes).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands

from bot.core.checks import organizer_only
from bot.core.errors import UserFacingError
from bot.features.events.announcement import link_button, publish_event_message, resolve_target_channel
from bot.features.events.create import parse_event_date
from bot.features.inhouse.constants import EVENT_TYPE, MODE_CHOICES, GameMode, get_mode
from bot.features.inhouse.group import inhouse_group
from bot.repositories.events import EventRepository
from bot.repositories.inhouse import InhouseRepository
from bot.utils import embeds
from bot.utils.embeds import Colors
from bot.utils.time import discord_full

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)


def check_capacity(mode: GameMode, places: int | None) -> str | None:
    """Valide le nombre de places ; renvoie un conseil (ou None) si ce n'est pas un multiple idéal."""
    if places is None:
        return None
    if places < mode.min_players:
        raise UserFacingError(
            f"En {mode.display}, il faut au moins **{mode.min_players}** places pour jouer une partie "
            f"(2 équipes de {mode.team_size})."
        )
    unit = mode.team_size * 2 if mode.key != "arena" else mode.team_size
    if places % unit:
        return (
            f"💡 {places} places n'est pas un multiple de {unit} : les derniers inscrits seront "
            "**remplaçants** lors de la création des équipes."
        )
    return None


@inhouse_group.command(name="creer", description="Créer une session d'inhouse et publier son annonce")
@app_commands.describe(
    mode="Mode de jeu : Faille (avec rôles), ARAM ou Arena (duos)",
    date="Date et heure locales : 28/09 21h, demain 20h30, samedi 18h…",
    places="Nombre de places (par défaut : 10 en Faille/ARAM, 16 en Arena). Au-delà : liste d'attente",
    titre="Titre de l'annonce (par défaut : « Inhouse <mode> »)",
    description="Infos affichées dans l'annonce (règles, draft, vocal…)",
    salon="Salon de l'annonce (par défaut : salon d'annonces configuré, sinon ce salon)",
)
@app_commands.choices(mode=MODE_CHOICES)
@organizer_only()
async def create_inhouse(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    date: str,
    places: Optional[app_commands.Range[int, 2, 200]] = None,
    titre: Optional[app_commands.Range[str, 2, 100]] = None,
    description: Optional[app_commands.Range[str, 1, 2000]] = None,
    salon: Optional[discord.TextChannel | discord.Thread] = None,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    assert interaction.guild is not None  # groupe guild_only
    game_mode = get_mode(mode.value)
    starts_at = parse_event_date(bot, date)
    tip = check_capacity(game_mode, places)
    max_players = places or game_mode.default_max
    title = (titre or f"Inhouse {game_mode.label}").strip()

    # Salon vérifié AVANT de créer quoi que ce soit : pas de session fantôme.
    channel = await resolve_target_channel(bot, interaction, salon)
    await interaction.response.defer(ephemeral=True, thinking=True)

    event = await EventRepository(bot.db).create(
        guild_id=interaction.guild.id,
        type=EVENT_TYPE,
        title=title,
        description=(description or "").strip() or None,
        starts_at=starts_at,
        max_participants=max_players,
        created_by=interaction.user.id,
    )
    try:
        await InhouseRepository(bot.db).create(event.id, game_mode.key)
    except Exception:
        await EventRepository(bot.db).delete(event.id)
        raise
    log.info(
        "Inhouse %s « %s » (%s, %d places) créé par %s (%s) pour le %s",
        event.id, title, game_mode.key, max_players, interaction.user, interaction.user.id, starts_at.isoformat(),
    )

    embed = discord.Embed(
        title="⚔️ Inhouse créé !",
        description=f"**{title}** · #{event.id}\n{game_mode.display} · {game_mode.format_text}\n📅 {discord_full(starts_at)}",
        color=Colors.SUCCESS,
    )
    embed.add_field(name="👥 Places", value=str(max_players), inline=True)
    view: discord.ui.View | None = None
    try:
        message = await publish_event_message(bot, event, channel)  # type: ignore[arg-type]
    except discord.HTTPException as exc:
        log.warning("Publication de l'annonce de l'inhouse %s impossible : %s", event.id, exc)
        embed.color = Colors.WARNING
        embed.add_field(
            name="⚠️ Annonce non publiée",
            value=f"Je n'ai pas pu poster dans {channel.mention}. Vérifie mes permissions puis utilise `/inhouse annoncer`.",
            inline=False,
        )
    else:
        embed.add_field(name="📣 Annonce", value=f"Publiée dans {message.channel.mention}", inline=True)  # type: ignore[union-attr]
        view = discord.ui.View()
        view.add_item(link_button(message.jump_url))
    if tip:
        embed.add_field(name="Places", value=tip, inline=False)

    steps = [
        "• Les joueurs lient leur compte LoL" + (" et choisissent leurs rôles" if game_mode.uses_roles else "")
        + " via les boutons de l'annonce, puis cliquent sur **Rejoindre**",
        "• `/inhouse inscrits` pour suivre les inscriptions (rangs, rôles, MultiGG)",
        "• `/inhouse equipes-generer` le moment venu, puis **Publier**",
        "• `/inhouse modifier` / `/inhouse inscriptions` pour ajuster · rappels automatiques ⏰",
    ]
    embed.add_field(name="💡 Et ensuite ?", value="\n".join(steps), inline=False)
    await embeds.reply(interaction, embed, ephemeral=True, view=view)
