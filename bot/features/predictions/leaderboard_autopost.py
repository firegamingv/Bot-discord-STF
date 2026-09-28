"""Classements publiés automatiquement.

Commandes (organisateurs) : ``/pronos-admin classement-auto ajouter | liste | supprimer``.
Tâche de fond : vérifie chaque minute les horaires ; ``last_posted_at`` en base empêche les
doublons (y compris après un redémarrage) et une échéance ratée depuis plus de 6 h est sautée.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.core.checks import organizer_only
from bot.core.errors import NotFoundError, UserFacingError
from bot.features.predictions.autocomplete import (
    competition_autocomplete,
    resolve_competition,
    tournament_autocomplete,
)
from bot.features.predictions.choices import BET_TYPE_CHOICES, PERIOD_CHOICES
from bot.features.predictions.group import pronos_admin_group
from bot.features.predictions.leaderboard_service import render_leaderboard
from bot.repositories.competitions import CompetitionRepository
from bot.repositories.leaderboard_schedules import (
    FREQ_DAILY,
    FREQ_WEEKLY,
    LeaderboardSchedule,
    LeaderboardScheduleRepository,
)
from bot.services.betting_rules import bet_type_label
from bot.services.periods import PERIOD_DAY, PERIOD_WEEK, WEEKDAYS_FR, is_due, period_label
from bot.utils import embeds
from bot.utils.embeds import Colors
from bot.utils.time import now_utc

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

autopost_group = app_commands.Group(
    name="classement-auto",
    description="Publier automatiquement un classement dans un salon",
    parent=pronos_admin_group,
)


def describe_schedule(s: LeaderboardSchedule, competition_name: str | None) -> str:
    when = (f"tous les jours à {s.hour:02d}h" if s.frequency == FREQ_DAILY
            else f"chaque {WEEKDAYS_FR[s.weekday]} à {s.hour:02d}h")
    parts = [f"<#{s.channel_id}>", when, period_label(s.period)]
    if competition_name:
        parts.append(f"🏆 {competition_name}")
    if s.tournament_name:
        parts.append(f"🎪 {s.tournament_name}")
    if s.bet_type:
        parts.append(bet_type_label(s.bet_type))
    return " • ".join(parts)


@autopost_group.command(name="ajouter", description="Programmer la publication automatique d'un classement")
@app_commands.describe(
    salon="Salon où publier le classement",
    frequence="Tous les jours ou une fois par semaine",
    jour="Jour de publication (si hebdomadaire ; lundi par défaut)",
    heure="Heure locale de publication (0-23, 20h par défaut)",
    periode="Période couverte (par défaut : le jour pour un quotidien, la semaine pour un hebdo)",
    competition="Limiter à une compétition",
    tournoi="Limiter à un tournoi / événement",
    type_pari="Limiter à un type de pari",
)
@app_commands.choices(
    frequence=[
        app_commands.Choice(name="Quotidien", value=FREQ_DAILY),
        app_commands.Choice(name="Hebdomadaire", value=FREQ_WEEKLY),
    ],
    jour=[app_commands.Choice(name=name.capitalize(), value=i) for i, name in enumerate(WEEKDAYS_FR)],
    periode=PERIOD_CHOICES,
    type_pari=BET_TYPE_CHOICES,
)
@app_commands.autocomplete(competition=competition_autocomplete, tournoi=tournament_autocomplete)
@organizer_only()
async def add_schedule(
    interaction: discord.Interaction,
    salon: discord.TextChannel,
    frequence: app_commands.Choice[str],
    jour: app_commands.Choice[int] | None = None,
    heure: app_commands.Range[int, 0, 23] = 20,
    periode: app_commands.Choice[str] | None = None,
    competition: str | None = None,
    tournoi: str | None = None,
    type_pari: app_commands.Choice[str] | None = None,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild = interaction.guild
    assert guild is not None
    perms = salon.permissions_for(guild.me)
    if not (perms.send_messages and perms.embed_links and perms.view_channel):
        raise UserFacingError(
            f"Je ne peux pas publier dans {salon.mention} : donne-moi les permissions "
            "*Voir le salon*, *Envoyer des messages* et *Intégrer des liens*, puis réessaie."
        )
    comp = await resolve_competition(bot, guild.id, competition)
    period = periode.value if periode else (PERIOD_DAY if frequence.value == FREQ_DAILY else PERIOD_WEEK)
    schedule = await LeaderboardScheduleRepository(bot.db).create(
        guild.id,
        channel_id=salon.id,
        frequency=frequence.value,
        weekday=jour.value if jour else 0,
        hour=heure,
        period=period,
        competition_id=comp.id if comp else None,
        tournament_name=tournoi.strip() if tournoi else None,
        bet_type=type_pari.value if type_pari else None,
        last_posted_at=now_utc(),  # pas de publication immédiate : on attend la prochaine échéance
    )
    log.info("Classement automatique #%s créé sur %s par %s", schedule.id, guild.id, interaction.user.id)
    await embeds.reply(interaction, embeds.success(
        f"Classement automatique **#{schedule.id}** programmé :\n{describe_schedule(schedule, comp.name if comp else None)}\n\n"
        "Pour voir ou retirer les publications : `/pronos-admin classement-auto liste` / `supprimer`.",
        title="📣 C'est noté !",
    ))


@autopost_group.command(name="liste", description="Voir les classements publiés automatiquement")
@organizer_only()
async def list_schedules(interaction: discord.Interaction) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild_id: int = interaction.guild_id  # type: ignore[assignment]
    schedules = await LeaderboardScheduleRepository(bot.db).list(guild_id)
    comps = {c.id: c.name for c in await CompetitionRepository(bot.db).list(guild_id, followed_only=False)}
    embed = discord.Embed(title="📣 Classements automatiques", color=Colors.ESPORT)
    if not schedules:
        embed.description = "Aucune publication programmée. Ajoute-en une avec `/pronos-admin classement-auto ajouter`."
    for s in schedules[:25]:
        embed.add_field(
            name=f"#{s.id}",
            value=describe_schedule(s, comps.get(s.competition_id) if s.competition_id else None)[:1024],
            inline=False,
        )
    await embeds.reply(interaction, embed)


async def schedule_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    if interaction.guild_id is None:
        return []
    out = []
    for s in await LeaderboardScheduleRepository(bot.db).list(interaction.guild_id):
        channel = bot.get_channel(s.channel_id)
        where = f"#{channel.name}" if isinstance(channel, discord.abc.GuildChannel) else "salon supprimé"
        when = "quotidien" if s.frequency == FREQ_DAILY else f"hebdo ({WEEKDAYS_FR[s.weekday]})"
        label = f"#{s.id} • {where} • {when} {s.hour:02d}h • {period_label(s.period)}"
        if current.lower() in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=s.id))
    return out[:25]


@autopost_group.command(name="supprimer", description="Arrêter une publication automatique de classement")
@app_commands.describe(publication="La publication à supprimer")
@app_commands.autocomplete(publication=schedule_autocomplete)
@organizer_only()
async def delete_schedule(interaction: discord.Interaction, publication: int) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    if not await LeaderboardScheduleRepository(bot.db).delete(interaction.guild_id, publication):  # type: ignore[arg-type]
        raise NotFoundError(
            f"Aucune publication automatique #{publication} sur ce serveur. "
            "Consulte la liste avec `/pronos-admin classement-auto liste`."
        )
    log.info("Classement automatique #%s supprimé par %s", publication, interaction.user.id)
    await embeds.reply(interaction, embeds.success(f"Publication automatique **#{publication}** supprimée."))


class LeaderboardAutopostTask(commands.Cog):
    """Publie les classements programmés à l'heure dite."""

    def __init__(self, bot: "STFBot") -> None:
        self.bot = bot
        self.loop.start()

    async def cog_unload(self) -> None:
        self.loop.cancel()

    @tasks.loop(minutes=1)
    async def loop(self) -> None:
        try:
            await self.run_once()
        except Exception:  # noqa: BLE001 - la boucle ne doit jamais mourir
            log.exception("Erreur dans la publication automatique des classements")

    @loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    async def run_once(self) -> None:
        repo = LeaderboardScheduleRepository(self.bot.db)
        tz = self.bot.config.timezone
        now = now_utc()
        for s in await repo.list_all():
            if self.bot.get_guild(s.guild_id) is None:
                continue
            if not is_due(s.frequency, weekday=s.weekday, hour=s.hour, tz=tz,
                          last_posted_at=s.last_posted_at, now=now):
                continue
            # On marque d'abord : en cas d'échec d'envoi, pas de spam de tentatives chaque minute.
            await repo.mark_posted(s.id, now)
            await self._post(s)

    async def _post(self, s: LeaderboardSchedule) -> None:
        channel = self.bot.get_channel(s.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            log.warning("Classement auto #%s : salon %s introuvable", s.id, s.channel_id)
            return
        embed = await render_leaderboard(self.bot, s.guild_id, s.filter)
        embed.title = f"📣 {embed.title}"
        embed.set_footer(text="Publication automatique • ton classement perso : /pronos classement")
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            log.info("Classement auto #%s publié dans %s", s.id, s.channel_id)
        except discord.HTTPException:
            log.warning("Classement auto #%s : envoi impossible dans %s", s.id, s.channel_id, exc_info=True)
