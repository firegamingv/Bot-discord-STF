"""Tâche de fond : chaque jour à 10:00 (heure locale), publie les matchs du jour.

Dans le salon des pronostics de chaque serveur : un message d'en-tête, puis une carte par
match avec ses boutons de pari persistants ; au-delà de 10 matchs, un récapitulatif unique
(les membres parient alors via ``/pronos matchs``).

Anti-doublon : le dernier jour publié par serveur est gardé en mémoire **et** dans un petit
fichier JSON (``<dossier de la base>/predictions_state.json``, écriture atomique) pour ne pas
republier après un redémarrage.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from bot.features.predictions.bet_buttons import build_match_card
from bot.features.predictions.embeds import fmt_odds
from bot.repositories.competitions import CompetitionRepository
from bot.repositories.matches import STATE_UPCOMING, Match, MatchRepository
from bot.services.periods import WEEKDAYS_FR, day_bounds
from bot.utils.embeds import Colors, Emojis, chunk_lines
from bot.utils.time import discord_ts, now_utc

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

POST_HOUR = 10
MAX_INDIVIDUAL_POSTS = 10
STATE_FILENAME = "predictions_state.json"


class DailyStateStore:
    """Mémorise, par serveur, la dernière date (locale, ISO) où les matchs ont été publiés."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, dict[str, str]] = {"daily_posted": {}}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("daily_posted"), dict):
                self.data["daily_posted"] = {str(k): str(v) for k, v in raw["daily_posted"].items()}
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.warning("Fichier d'état %s illisible : il sera recréé", self.path, exc_info=True)

    def posted_on(self, guild_id: int) -> str | None:
        return self.data["daily_posted"].get(str(guild_id))

    def mark(self, guild_id: int, day_iso: str) -> None:
        self.data["daily_posted"][str(guild_id)] = day_iso
        self._save()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".predictions_state.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            log.warning("Impossible d'écrire %s (anti-doublon en mémoire seulement)", self.path, exc_info=True)


class DailyMatchesTask(commands.Cog):
    def __init__(self, bot: "STFBot") -> None:
        self.bot = bot
        self.state = DailyStateStore(Path(bot.config.database_path).parent / STATE_FILENAME)

    async def cog_load(self) -> None:
        self.daily_loop.start()

    async def cog_unload(self) -> None:
        self.daily_loop.cancel()

    @tasks.loop(minutes=1)
    async def daily_loop(self) -> None:
        try:
            await self.run_once()
        except Exception:  # noqa: BLE001 - la boucle ne doit jamais mourir
            log.exception("Erreur pendant la publication des matchs du jour")

    @daily_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    async def run_once(self) -> None:
        tz = self.bot.config.timezone
        local_now = now_utc().astimezone(tz)
        if local_now.hour < POST_HOUR:
            return
        today = local_now.date().isoformat()
        for guild in self.bot.guilds:
            if self.state.posted_on(guild.id) == today:
                continue
            settings = await self.bot.settings.get(guild.id)
            if not settings.predictions_channel_id:
                continue  # pas de salon : on réessaiera si un organisateur en configure un aujourd'hui
            channel = guild.get_channel(settings.predictions_channel_id)
            if not isinstance(channel, discord.abc.Messageable):
                continue
            self.state.mark(guild.id, today)  # d'abord : jamais de double publication
            await self.post_matches(guild.id, channel)

    async def post_matches(self, guild_id: int, channel: discord.abc.Messageable) -> int:
        tz = self.bot.config.timezone
        _, end = day_bounds(tz)
        matches = await MatchRepository(self.bot.db).list_between(
            guild_id, now_utc(), end, states=(STATE_UPCOMING,), limit=50
        )
        if not matches:
            return 0
        comps = {c.id: c.name for c in await CompetitionRepository(self.bot.db).list(guild_id, followed_only=False)}
        settings = await self.bot.settings.get(guild_id)
        local = now_utc().astimezone(tz)
        header = discord.Embed(
            title=f"{Emojis.CALENDAR} Les matchs du {WEEKDAYS_FR[local.weekday()]} {local:%d/%m}",
            description=(
                f"**{len(matches)} match{'s' if len(matches) > 1 else ''}** au programme aujourd'hui ! "
                f"Cotes : vainqueur {fmt_odds(settings.odds_winner)} • score exact {fmt_odds(settings.odds_exact_score)}.\n"
                "Les paris ferment au début de chaque match. Bonne chance ! 🍀"
            ),
            color=Colors.ESPORT,
        )
        try:
            if len(matches) <= MAX_INDIVIDUAL_POSTS:
                header.set_footer(text="Clique sur une équipe pour parier • règles : /pronos regles")
                await channel.send(embed=header)
                for match in matches:
                    embed, view = await build_match_card(self.bot, match)
                    await channel.send(embed=embed, view=view)
            else:
                lines = [self._recap_line(m, comps) for m in matches]
                for i, chunk in enumerate(chunk_lines(lines)[:5]):
                    header.add_field(name="Programme" if i == 0 else "​", value=chunk, inline=False)
                header.set_footer(text="Pour parier : /pronos matchs • règles : /pronos regles")
                await channel.send(embed=header)
        except discord.HTTPException:
            log.warning("Publication des matchs du jour impossible (serveur %s)", guild_id, exc_info=True)
            return 0
        log.info("Matchs du jour publiés sur %s : %d match(s)", guild_id, len(matches))
        return len(matches)

    @staticmethod
    def _recap_line(match: Match, comps: dict[int, str]) -> str:
        return (f"{discord_ts(match.starts_at, 't')} {Emojis.SWORDS} **{match.team_code(1)}** vs "
                f"**{match.team_code(2)}** · Bo{match.best_of} · {comps.get(match.competition_id, '')}")
