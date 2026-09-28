"""``/pronos matchs [competition] [jour]`` : liste éphémère des matchs avec menu pour parier."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.error_reporting import BaseView
from bot.core.errors import NotFoundError
from bot.features.predictions.autocomplete import competition_autocomplete, resolve_competition
from bot.features.predictions.bet_buttons import build_match_card
from bot.features.predictions.embeds import choice_label, fmt_points, short_local
from bot.features.predictions.group import pronos_group
from bot.features.predictions.wallet_service import ensure_wallet
from bot.repositories.competitions import CompetitionRepository
from bot.repositories.matches import STATE_LABELS, Match, MatchRepository
from bot.repositories.predictions import PredictionRepository
from bot.services.betting_rules import bet_type_label
from bot.services.periods import day_bounds
from bot.utils.embeds import Colors, Emojis, chunk_lines
from bot.utils.time import discord_ts, now_utc

if TYPE_CHECKING:
    from bot.core.bot import STFBot

DAY_TODAY = "today"
DAY_TOMORROW = "tomorrow"
DAY_WEEK = "week"
DAY_LABELS = {DAY_TODAY: "aujourd'hui", DAY_TOMORROW: "demain", DAY_WEEK: "des 7 prochains jours"}


class MatchPicker(discord.ui.Select):
    def __init__(self, bot: "STFBot", matches: list[Match]) -> None:
        options = [
            discord.SelectOption(
                label=f"{m.team_code(1)} vs {m.team_code(2)} (Bo{m.best_of})"[:100],
                description=f"{short_local(m.starts_at, bot.config.timezone)} • {m.tournament_name or ''}"[:100],
                value=str(m.id),
                emoji="⚔️",
            )
            for m in matches[:25]
        ]
        super().__init__(placeholder="🎲 Choisis un match pour parier…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: STFBot = interaction.client  # type: ignore[assignment]
        match = await MatchRepository(bot.db).get_in_guild(interaction.guild_id, int(self.values[0]))  # type: ignore[arg-type]
        if match is None:
            raise NotFoundError("Ce match n'existe plus.")
        embed, view = await build_match_card(bot, match)
        mine = await PredictionRepository(bot.db).list_user_bets_for_match(match.id, interaction.user.id)
        content = "Clique sur une équipe (ou 🎯 pour le score exact) pour parier :"
        if mine:
            lines = [
                f"• {bet_type_label(p.bet_type)} : {choice_label(match, p.bet_type, p.choice)} — {fmt_points(p.stake)}"
                for p in mine
            ]
            content = "🎟️ **Tes paris sur ce match** (clique à nouveau pour les modifier) :\n" + "\n".join(lines)
        await interaction.response.send_message(content, embed=embed, view=view, ephemeral=True)


class MatchPickerView(BaseView):
    def __init__(self, bot: "STFBot", matches: list[Match]) -> None:
        super().__init__(timeout=600)
        self.add_item(MatchPicker(bot, matches))


def _line(match: Match, comp_names: dict[int, str]) -> str:
    comp = comp_names.get(match.competition_id, "")
    status = "" if match.is_open() else f" • {STATE_LABELS.get(match.state, match.state)}"
    if match.score_text and match.state in ("live", "completed"):
        status += f" **{match.score_text}**"
    return (
        f"{Emojis.SWORDS} **{match.team_code(1)}** vs **{match.team_code(2)}** · Bo{match.best_of} · "
        f"{discord_ts(match.starts_at, 't')} ({discord_ts(match.starts_at, 'R')})\n"
        f"└ {comp}{' • ' + match.tournament_name if match.tournament_name and match.tournament_name != comp else ''}{status}"
    )


@pronos_group.command(name="matchs", description="📅 Voir les matchs à venir et parier dessus")
@app_commands.describe(competition="Filtrer sur une compétition", jour="Quels matchs afficher (par défaut : aujourd'hui)")
@app_commands.autocomplete(competition=competition_autocomplete)
@app_commands.choices(jour=[
    app_commands.Choice(name="Aujourd'hui", value=DAY_TODAY),
    app_commands.Choice(name="Demain", value=DAY_TOMORROW),
    app_commands.Choice(name="7 prochains jours", value=DAY_WEEK),
])
async def list_matches(
    interaction: discord.Interaction,
    competition: str | None = None,
    jour: app_commands.Choice[str] | None = None,
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    guild_id: int = interaction.guild_id  # type: ignore[assignment]
    tz = bot.config.timezone
    wallet = await ensure_wallet(bot, guild_id, interaction.user)
    comp = await resolve_competition(bot, guild_id, competition)
    day = jour.value if jour else DAY_TODAY

    if day == DAY_TOMORROW:
        start, end = day_bounds(tz, offset_days=1)
    elif day == DAY_WEEK:
        start, end = now_utc() - timedelta(hours=3), day_bounds(tz, offset_days=7)[1]
    else:
        start, end = day_bounds(tz)
    repo = MatchRepository(bot.db)
    matches = await repo.list_between(guild_id, start, end, competition_id=comp.id if comp else None, limit=60)
    comps = await CompetitionRepository(bot.db).list(guild_id, followed_only=False)
    comp_names = {c.id: c.name for c in comps}

    title = f"📅 Matchs {DAY_LABELS[day]}" + (f" — {comp.name}" if comp else "")
    embed = discord.Embed(title=title, color=Colors.ESPORT)
    if wallet.notice:
        embed.description = wallet.notice
    if not matches:
        tip = "Essaie `jour: 7 prochains jours`" if day != DAY_WEEK else "Reviens plus tard"
        if not comps:
            tip = "Aucune compétition n'est suivie : un organisateur doit en choisir avec `/pronos-admin competitions`"
        embed.add_field(name="Aucun match 😴", value=f"{tip} !", inline=False)
        embed.set_footer(text=f"Ton solde : {wallet.balance} 🪙")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    lines = [_line(m, comp_names) for m in matches]
    for i, chunk in enumerate(chunk_lines(lines)[:4]):
        embed.add_field(name="Programme" if i == 0 else "​", value=chunk, inline=False)
    if len(matches) > 25:
        embed.add_field(name="​", value="… liste tronquée : filtre par compétition pour tout voir.", inline=False)
    bettable = [m for m in matches if m.is_open()]
    embed.set_footer(text=f"💼 Ton solde : {wallet.balance} 🪙 • {len(bettable)} match(s) ouvert(s) aux paris")
    view = MatchPickerView(bot, bettable) if bettable else None
    if view is None:
        embed.add_field(name=f"{Emojis.LOCK} Paris fermés", value="Tous ces matchs ont déjà commencé.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
