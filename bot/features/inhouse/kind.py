"""Type d'événement « Inhouse LoL » : se branche sur le système d'événements générique.

Les boutons génériques (Rejoindre / Quitter / Voir les inscrits) appellent ces hooks :
- ``build_embed``       : annonce détaillée (mode, date, places, inscrits avec Riot ID / rang /
  rôles, liste d'attente, résumé des rôles, lien MultiGG) ;
- ``extra_components``  : boutons « Lier mon compte LoL », « Choisir mes rôles » (Faille) et
  « Voir les équipes » (une fois publiées) ;
- ``check_can_join``    : compte LoL lié obligatoire, rôles obligatoires en Faille ;
- ``on_joined``         : rappel des rôles + rafraîchissement du rang en tâche de fond ;
- ``on_left``           : retrait des équipes + avertissement ;
- ``on_deleted``        : suppression du message des équipes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord

from bot.core.errors import UserFacingError
from bot.features.account.link import LinkAccountButton
from bot.features.account.rank_refresh import refresh_ranks
from bot.features.account.role_picker import RolesButton
from bot.features.events.kinds import EventKind
from bot.features.inhouse.constants import EVENT_TYPE, get_mode, team_title
from bot.features.inhouse.players import (
    load_players,
    main_role_counts,
    missing_roles_text,
    multigg_links,
    player_line,
    role_summary,
)
from bot.features.inhouse.teams_display import TeamsButton
from bot.repositories.inhouse import InhouseRepository
from bot.repositories.inhouse_teams import InhouseTeamRepository
from bot.repositories.participants import REGISTERED, WAITLIST
from bot.repositories.player_roles import PlayerRoleRepository, role_label
from bot.repositories.riot_accounts import RiotAccountRepository
from bot.utils.embeds import Colors, Emojis, chunk_lines
from bot.utils.time import discord_full

if TYPE_CHECKING:
    from bot.core.bot import STFBot
    from bot.repositories.events import Event
    from bot.repositories.participants import Participant

log = logging.getLogger(__name__)

# Garde une référence vers les tâches de fond (sinon le ramasse-miettes peut les annuler).
_background: set[asyncio.Task] = set()

MAX_PLAYER_FIELDS = 4
MAX_WAITLIST_LINES = 15


def _spawn(coro, name: str) -> None:
    task = asyncio.create_task(coro, name=name)
    _background.add(task)

    def _done(t: asyncio.Task) -> None:
        _background.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.warning("Tâche de fond %s en échec", name, exc_info=t.exception())

    task.add_done_callback(_done)


async def _refresh_and_update(bot: "STFBot", event_id: int, discord_ids: list[int]) -> None:
    """Rafraîchit le rang des joueurs puis met à jour l'annonce (rang affiché)."""
    from bot.features.events.announcement import refresh_event_message

    repo = RiotAccountRepository(bot.db)
    before = {uid: a.rank_updated_at for uid, a in (await repo.get_many(discord_ids)).items()}
    await refresh_ranks(bot, discord_ids)
    after = {uid: a.rank_updated_at for uid, a in (await repo.get_many(discord_ids)).items()}
    if after != before:
        await refresh_event_message(bot, event_id)


class InhouseKind(EventKind):
    key = EVENT_TYPE
    label = "Inhouse LoL"
    emoji = "⚔️"
    color = Colors.INHOUSE

    # ------------------------------------------------------------------ annonce
    async def build_embed(
        self, bot: "STFBot", event: "Event", participants: list["Participant"]
    ) -> discord.Embed | None:
        session = await InhouseRepository(bot.db).get(event.id)
        mode = get_mode(session.game_mode if session else None)
        registered = [p.discord_id for p in participants if p.status == REGISTERED]
        waiting = [p.discord_id for p in participants if p.status == WAITLIST]
        cards = await load_players(bot, [*registered, *waiting])

        embed = discord.Embed(
            title=f"{self.emoji} {event.title}"[:256],
            description=(event.description or "")[:1500] or None,
            color=self.color if event.is_active else Colors.NEUTRAL,
            timestamp=event.starts_at,
        )
        embed.add_field(name="🎮 Mode", value=f"**{mode.display}**\n{mode.format_text}", inline=True)
        embed.add_field(name=f"{Emojis.CALENDAR} Date", value=discord_full(event.starts_at), inline=True)

        # places
        if event.max_participants is None:
            places = f"**{len(registered)}** inscrits · places illimitées"
        else:
            free = event.max_participants - len(registered)
            places = f"**{len(registered)}/{event.max_participants}**"
            places += f" · {free} place{'s' if free > 1 else ''} libre{'s' if free > 1 else ''}" if free > 0 else " · complet"
            if waiting:
                places += f" · ⏳ {len(waiting)} en attente"
        embed.add_field(name=f"{Emojis.PEOPLE} Places", value=places, inline=False)

        open_now = event.registration_open and event.is_active and not event.has_started
        state = f"{Emojis.UNLOCK} Ouvertes" if open_now else f"{Emojis.LOCK} Fermées"
        if event.status == "cancelled":
            state = "❌ Inhouse annulé"
        elif event.status == "finished":
            state = "🏁 Inhouse terminé"
        elif event.status == "ongoing":
            state += " · 🟢 en cours"
        embed.add_field(name="📝 Inscriptions", value=state, inline=True)
        embed.add_field(name="🎤 Organisé par", value=f"<@{event.created_by}>", inline=True)

        # inscrits
        lines = [player_line(cards[uid], index=i, show_roles=mode.uses_roles) for i, uid in enumerate(registered, start=1)]
        if lines:
            chunks = chunk_lines(lines)
            for idx, chunk in enumerate(chunks[:MAX_PLAYER_FIELDS]):
                name = f"{Emojis.SUCCESS} Inscrits ({len(registered)})" if idx == 0 else "​"
                embed.add_field(name=name, value=chunk, inline=False)
            if len(chunks) > MAX_PLAYER_FIELDS:
                embed.add_field(name="​", value="… liste complète avec « Voir les inscrits »", inline=False)
        else:
            embed.add_field(
                name=f"{Emojis.SUCCESS} Inscrits (0)",
                value="*Personne pour l'instant… lance-toi avec « Rejoindre » !*",
                inline=False,
            )
        if waiting:
            wl = [f"`{i:>2}.` <@{uid}>" for i, uid in enumerate(waiting[:MAX_WAITLIST_LINES], start=1)]
            if len(waiting) > MAX_WAITLIST_LINES:
                wl.append(f"… et {len(waiting) - MAX_WAITLIST_LINES} autre(s)")
            embed.add_field(name=f"⏳ Liste d'attente ({len(waiting)})", value=chunk_lines(wl)[0], inline=False)

        # rôles (Faille) : ce qui manque pour des parties complètes
        if mode.uses_roles and registered:
            counts = main_role_counts(cards[uid] for uid in registered)
            target = event.max_participants or len(registered)
            needed = max(2, (target // mode.players_per_match) * 2)
            value = role_summary(counts)
            if missing := missing_roles_text(counts, needed):
                value += f"\n{missing}"
            else:
                value += "\n✅ Tous les rôles sont couverts !"
            embed.add_field(name="🧩 Rôles principaux", value=value[:1024], inline=False)

        if registered and (links := multigg_links(bot, cards, registered, label="MultiGG des inscrits")):
            embed.add_field(name=f"{Emojis.LINK} Liens", value=links[:1024], inline=False)

        if session and session.teams_published:
            embed.add_field(
                name="⚔️ Équipes",
                value="Les équipes sont prêtes : clique sur **« Voir les équipes »** !",
                inline=False,
            )

        footer = f"Inhouse #{event.id}"
        if open_now:
            footer += " · Lie ton compte LoL" + (" et choisis tes rôles" if mode.uses_roles else "")
            footer += ", puis clique sur « Rejoindre » !"
        embed.set_footer(text=footer)
        return embed

    async def extra_components(self, bot: "STFBot", event: "Event") -> list[discord.ui.Item]:
        session = await InhouseRepository(bot.db).get(event.id)
        mode = get_mode(session.game_mode if session else None)
        items: list[discord.ui.Item] = []
        if event.is_active:
            items.append(LinkAccountButton(row=1))
            if mode.uses_roles:
                items.append(RolesButton(row=1))
        if session and session.teams_published:
            items.append(TeamsButton(event.id, row=1))
        return items

    # ------------------------------------------------------------------ inscriptions
    async def check_can_join(self, bot: "STFBot", interaction: discord.Interaction, event: "Event") -> None:
        account = await RiotAccountRepository(bot.db).get(interaction.user.id)
        if account is None:
            raise UserFacingError(
                "Pour participer à un inhouse, associe d'abord ton compte League of Legends : "
                "clique sur **🔗 Lier mon compte LoL** sous l'annonce (ou tape `/compte lier`), "
                "puis reclique sur **Rejoindre**.",
                title="Compte LoL requis",
            )
        session = await InhouseRepository(bot.db).get(event.id)
        mode = get_mode(session.game_mode if session else None)
        if mode.uses_roles and not await PlayerRoleRepository(bot.db).get(interaction.user.id):
            raise UserFacingError(
                "Cet inhouse se joue sur la **Faille de l'invocateur** : dis-nous quels rôles tu joues ! "
                "Clique sur **🎮 Choisir mes rôles** sous l'annonce (ou tape `/compte roles`), "
                "puis reclique sur **Rejoindre**.",
                title="Choisis tes rôles",
            )

    async def on_joined(
        self, bot: "STFBot", interaction: discord.Interaction, event: "Event", participant: "Participant"
    ) -> str | None:
        user_id = interaction.user.id
        _spawn(_refresh_and_update(bot, event.id, [user_id]), name=f"inhouse-rank-{event.id}-{user_id}")

        session = await InhouseRepository(bot.db).get(event.id)
        mode = get_mode(session.game_mode if session else None)
        account = await RiotAccountRepository(bot.db).get(user_id)
        lines = [f"🎮 Mode : **{mode.display}**"]
        if account is not None:
            lines.append(f"🔗 Compte : **{account.riot_id}**")
        if mode.uses_roles:
            roles = await PlayerRoleRepository(bot.db).get(user_id)
            lines.append("🧩 Tes rôles : " + (" > ".join(role_label(r) for r in roles) or "aucun"))
            lines.append("*Modifiables à tout moment avec le bouton « Choisir mes rôles ».*")
        if session and await InhouseTeamRepository(bot.db).has_teams(event.id):
            lines.append("⚠️ Les équipes sont déjà constituées : un organisateur t'intégrera si besoin.")
        return "\n".join(lines)

    async def on_left(self, bot: "STFBot", event: "Event", discord_id: int) -> None:
        removed = await InhouseTeamRepository(bot.db).remove_player(event.id, discord_id)
        if removed is None:
            return
        session = await InhouseRepository(bot.db).get(event.id)
        mode = get_mode(session.game_mode if session else None)
        where = "des remplaçants" if removed.is_substitute else f"de l'{team_title(removed.team_index, mode.key)}"
        log.info("Membre %s retiré %s (inhouse %s) après désinscription", discord_id, where, event.id)
        if removed.is_substitute or session is None or not session.teams_published:
            return
        # Les équipes sont publiées : on prévient dans le salon des équipes.
        channel = bot.get_channel(session.teams_channel_id or 0)
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.send(
                f"⚠️ <@{discord_id}> s'est désinscrit·e de **{event.title}** et a été retiré·e {where}.\n"
                "Organisateurs : complétez avec `/inhouse equipes-deplacer` (un remplaçant par exemple) "
                "ou regénérez avec `/inhouse equipes-generer`, puis `/inhouse equipes-publier`.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as exc:
            log.info("Avertissement de désistement non publié (inhouse %s) : %s", event.id, exc)

    async def on_deleted(self, bot: "STFBot", event: "Event") -> None:
        session = await InhouseRepository(bot.db).get(event.id)
        if session is None or session.teams_message_id is None or session.teams_channel_id is None:
            return
        channel = bot.get_channel(session.teams_channel_id)
        if channel is None or not hasattr(channel, "get_partial_message"):
            return
        try:
            await channel.get_partial_message(session.teams_message_id).delete()  # type: ignore[attr-defined]
        except discord.HTTPException as exc:
            log.info("Message des équipes de l'inhouse %s non supprimé : %s", event.id, exc)
