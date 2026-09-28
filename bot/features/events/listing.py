"""``/evenement liste`` : les événements à venir, paginés (10 par page)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands

from bot.core.error_reporting import BaseView
from bot.core.errors import UserFacingError
from bot.features.events.announcement import capacity_label, event_message_url
from bot.features.events.group import event_group
from bot.features.events.kinds import all_kinds, get_kind
from bot.repositories.events import STATUS_ONGOING, Event, EventRepository
from bot.repositories.participants import ParticipantRepository
from bot.utils import embeds
from bot.utils.embeds import Colors, Emojis
from bot.utils.time import discord_ts

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

PAGE_SIZE = 10
MAX_EVENTS = 100


def format_event_line(event: Event, registered: int) -> str:
    """Une ligne de la liste : emoji, titre cliquable, date relative, places, état des inscriptions."""
    kind = get_kind(event.type)
    url = event_message_url(event)
    title = f"[{event.title}]({url})" if url else event.title
    when = "🟢 **en cours**" if event.status == STATUS_ONGOING else discord_ts(event.starts_at, "R")
    lock = Emojis.UNLOCK if event.registration_open and event.status != STATUS_ONGOING else Emojis.LOCK
    return (
        f"{kind.emoji} **{title}** · `#{event.id}`\n"
        f"┗ {when} · {discord_ts(event.starts_at, 'f')} · {Emojis.PEOPLE} {capacity_label(event, registered)} · {lock}"
    )


def build_page(lines: list[str], page: int, total_pages: int, total: int, type_label: str | None) -> discord.Embed:
    title = f"{Emojis.CALENDAR} Événements à venir"
    if type_label:
        title += f" · {type_label}"
    embed = discord.Embed(title=title, color=Colors.PRIMARY)
    start = page * PAGE_SIZE
    embed.description = "\n\n".join(lines[start : start + PAGE_SIZE])[:4096]
    footer = f"{total} événement(s)"
    if total_pages > 1:
        footer += f" · page {page + 1}/{total_pages}"
    footer += " · 🔓 inscriptions ouvertes · 🔒 fermées · /evenement voir pour les détails"
    embed.set_footer(text=footer)
    return embed


class PaginationView(BaseView):
    """Boutons ◀ ▶ pour feuilleter la liste (utilisables par l'auteur uniquement)."""

    def __init__(self, lines: list[str], author_id: int, type_label: str | None) -> None:
        super().__init__(timeout=300)
        self.lines = lines
        self.author_id = author_id
        self.type_label = type_label
        self.page = 0
        self.total_pages = max(1, -(-len(lines) // PAGE_SIZE))
        self.message: discord.InteractionMessage | None = None
        self._sync_buttons()

    def current_embed(self) -> discord.Embed:
        return build_page(self.lines, self.page, self.total_pages, len(self.lines), self.type_label)

    def _sync_buttons(self) -> None:
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= self.total_pages - 1
        self.counter.label = f"{self.page + 1}/{self.total_pages}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=embeds.info("Lance ta propre liste avec `/evenement liste` 😉"), ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message is not None:
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def counter(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = min(self.total_pages - 1, self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)


async def type_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    current = current.lower()
    return [
        app_commands.Choice(name=f"{k.emoji} {k.label}", value=k.key)
        for k in all_kinds()
        if current in k.label.lower() or current in k.key
    ][:25]


@event_group.command(name="liste", description="Voir les événements à venir")
@app_commands.describe(
    type="Filtrer par type d'événement (ex. Inhouse)",
    publier="Afficher la liste à tout le salon plutôt qu'à toi seul·e",
)
@app_commands.autocomplete(type=type_autocomplete)
async def list_events(interaction: discord.Interaction, type: Optional[str] = None, publier: bool = False) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    assert interaction.guild_id is not None
    type_label = None
    if type is not None:
        if type not in {k.key for k in all_kinds()}:
            raise UserFacingError("Type d'événement inconnu : choisis-en un dans la liste proposée.")
        kind = get_kind(type)
        type_label = f"{kind.emoji} {kind.label}"

    events = await EventRepository(bot.db).list_upcoming(interaction.guild_id, type=type, limit=MAX_EVENTS)
    if not events:
        text = "Aucun événement à venir pour le moment 😴"
        if type_label:
            text = f"Aucun événement {type_label} à venir pour le moment 😴"
        await embeds.reply(
            interaction,
            embeds.info(text + "\nLes organisateurs peuvent en créer un avec `/evenement creer`.",
                        title=f"{Emojis.CALENDAR} Événements à venir"),
            ephemeral=True,
        )
        return

    participants = ParticipantRepository(bot.db)
    lines = [format_event_line(e, await participants.count(e.id)) for e in events]
    view = PaginationView(lines, interaction.user.id, type_label)
    if view.total_pages == 1:
        await interaction.response.send_message(embed=view.current_embed(), ephemeral=not publier)
        return
    await interaction.response.send_message(embed=view.current_embed(), view=view, ephemeral=not publier)
    view.message = await interaction.original_response()
