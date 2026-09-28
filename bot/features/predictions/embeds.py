"""Mise en forme des pronostics : embeds de match, de pari, de résultat, de classement, de stats."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from bot.repositories.competitions import Competition
from bot.repositories.matches import STATE_LABELS, Match
from bot.repositories.points import BalanceEntry, LeaderboardEntry, LeaderboardFilter, UserStats
from bot.repositories.predictions import STATUS_LABELS, Prediction
from bot.services.betting_rules import (
    BET_EXACT_SCORE,
    BET_WINNER,
    bet_type_label,
    exact_score_allowed,
)
from bot.services.periods import WEEKDAYS_FR_SHORT, period_label
from bot.utils.embeds import Colors, Emojis
from bot.utils.time import discord_full, discord_ts

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
TEAM1_EMOJI = "🔵"
TEAM2_EMOJI = "🔴"


# ---------------------------------------------------------------------------- petits formats
def fmt_points(amount: int, *, signed: bool = False) -> str:
    text = f"{abs(amount):,}".replace(",", " ")
    sign = "-" if amount < 0 else ("+" if signed and amount > 0 else "")
    return f"{sign}{text} {Emojis.COIN}"


def fmt_odds(odds: float) -> str:
    return f"x{odds:.2f}".rstrip("0").rstrip(".")


def short_local(dt: datetime, tz: ZoneInfo) -> str:
    """``sam. 04/10 18:00`` (pour l'autocomplétion, qui n'affiche pas les timestamps Discord)."""
    local = dt.astimezone(tz)
    return f"{WEEKDAYS_FR_SHORT[local.weekday()]} {local:%d/%m %H:%M}"


def rank_prefix(rank: int) -> str:
    return MEDALS.get(rank, f"`{rank:>2}.`")


def choice_label(match: Match, bet_type: str, choice: str) -> str:
    if bet_type == BET_WINNER:
        return f"{TEAM1_EMOJI if choice == '1' else TEAM2_EMOJI} {match.team_name(choice)}"
    a, b = choice.split("-") if "-" in choice else (choice, "?")
    return f"{match.team_code(1)} **{a} - {b}** {match.team_code(2)}"


def match_context_line(match: Match, competition: Competition | None) -> str:
    parts = [f"🏆 {competition.name}" if competition else None, match.tournament_name, match.block_name]
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return " • ".join(seen)


def describe_filter(flt: LeaderboardFilter, competition: Competition | None) -> str:
    parts = [f"📆 {period_label(flt.period)}"]
    if competition:
        parts.append(f"🏆 {competition.name}")
    if flt.tournament_name:
        parts.append(f"🎪 {flt.tournament_name}")
    if flt.bet_type:
        parts.append(bet_type_label(flt.bet_type))
    return " • ".join(parts)


# ---------------------------------------------------------------------------- match
def build_match_embed(
    match: Match,
    competition: Competition | None,
    counts: dict[tuple[str, str], int],
    *,
    odds_winner: float,
    odds_exact_score: float,
) -> discord.Embed:
    """Carte d'un match : équipes, format, heure, compétition, cotes, répartition des paris."""
    t1 = f"{TEAM1_EMOJI} **{match.team1_name}**" + (f" `{match.team1_code}`" if match.team1_code else "")
    t2 = f"{TEAM2_EMOJI} **{match.team2_name}**" + (f" `{match.team2_code}`" if match.team2_code else "")
    color = Colors.ESPORT if match.is_open() else Colors.NEUTRAL
    embed = discord.Embed(
        title=f"{Emojis.SWORDS} {match.team_code(1)} vs {match.team_code(2)}",
        description=f"{t1}\n*contre*\n{t2}",
        color=color,
    )
    context = match_context_line(match, competition)
    if context:
        embed.set_author(name=context[:256], icon_url=competition.image_url if competition and competition.image_url else None)
    embed.add_field(name=f"{Emojis.CLOCK} Début", value=discord_full(match.starts_at), inline=False)
    embed.add_field(name="🎮 Format", value=f"Best of {match.best_of}", inline=True)
    state = STATE_LABELS.get(match.state, match.state)
    if match.state == "upcoming" and not match.is_open():
        state = f"{Emojis.LOCK} Paris fermés"
    embed.add_field(name="📡 Statut", value=state, inline=True)
    if match.score_text and match.state in ("live", "completed"):
        embed.add_field(name="📊 Score", value=f"**{match.team_code(1)} {match.score_text} {match.team_code(2)}**", inline=True)

    odds_lines = [f"🏆 Vainqueur : **{fmt_odds(odds_winner)}**"]
    if exact_score_allowed(match.best_of):
        odds_lines.append(f"🎯 Score exact : **{fmt_odds(odds_exact_score)}**")
    embed.add_field(name="💹 Cotes", value="\n".join(odds_lines), inline=True)

    n1 = counts.get((BET_WINNER, "1"), 0)
    n2 = counts.get((BET_WINNER, "2"), 0)
    n_score = sum(n for (bt, _), n in counts.items() if bt == BET_EXACT_SCORE)
    total = n1 + n2
    if total:
        pct1 = round(100 * n1 / total)
        bar_len = 12
        filled = round(bar_len * n1 / total)
        bar = "🟦" * filled + "🟥" * (bar_len - filled)
        split = f"{bar}\n{match.team_code(1)} **{n1}** ({pct1} %) • {match.team_code(2)} **{n2}** ({100 - pct1} %)"
    else:
        split = "Aucun pari pour l'instant : sois le premier ! 🎉"
    if n_score:
        split += f"\n🎯 {n_score} pari{'s' if n_score > 1 else ''} sur le score exact"
    embed.add_field(name=f"{Emojis.PEOPLE} Paris des membres", value=split, inline=False)
    if match.is_open():
        embed.set_footer(text=f"Paris ouverts jusqu'au début du match • Match #{match.id}")
    else:
        embed.set_footer(text=f"Match #{match.id}")
    return embed


# ---------------------------------------------------------------------------- pari
def build_bet_confirmation_embed(
    match: Match,
    prediction: Prediction,
    *,
    balance: int,
    previous_stake: int | None,
    notice: str | None,
) -> discord.Embed:
    title = "✏️ Pari modifié !" if previous_stake is not None else "🎟️ Pari enregistré !"
    embed = discord.Embed(title=title, color=Colors.SUCCESS)
    embed.description = (
        f"{Emojis.SWORDS} **{match.title}** — {discord_ts(match.starts_at, 'f')}\n"
        f"{bet_type_label(prediction.bet_type)} : {choice_label(match, prediction.bet_type, prediction.choice)}"
    )
    embed.add_field(name="Mise", value=fmt_points(prediction.stake), inline=True)
    embed.add_field(name="Cote", value=fmt_odds(prediction.odds), inline=True)
    embed.add_field(name="Gain potentiel", value=f"**{fmt_points(prediction.potential_payout)}**", inline=True)
    if previous_stake is not None:
        embed.add_field(
            name="↩️ Ancienne mise", value=f"{fmt_points(previous_stake)} remboursée", inline=True
        )
    embed.add_field(name="💼 Solde restant", value=fmt_points(balance), inline=True)
    if notice:
        embed.add_field(name="🎁 Bonus", value=notice, inline=False)
    embed.set_footer(text="Tu peux modifier ton pari jusqu'au début du match. Bonne chance ! 🍀")
    return embed


def format_prediction_line(prediction: Prediction, match: Match | None) -> str:
    if match is None:
        return f"{STATUS_LABELS.get(prediction.status)} • pari #{prediction.id} (match supprimé)"
    head = f"**{match.short_title}** {discord_ts(match.starts_at, 'R' if prediction.status == 'pending' else 'd')}"
    body = f"{bet_type_label(prediction.bet_type)} {choice_label(match, prediction.bet_type, prediction.choice)}"
    money = f"{fmt_points(prediction.stake)} {fmt_odds(prediction.odds)}"
    if prediction.status == "pending":
        tail = f"→ gain potentiel **{fmt_points(prediction.potential_payout)}**"
    elif prediction.status == "won":
        tail = f"→ **{fmt_points(prediction.payout - prediction.stake, signed=True)}**"
    elif prediction.status == "lost":
        tail = f"→ {fmt_points(-prediction.stake)}"
    else:
        tail = "→ mise rendue"
    return f"{STATUS_LABELS.get(prediction.status, prediction.status)} {head}\n└ {body} • {money} {tail}"


# ---------------------------------------------------------------------------- résultat
def build_result_embed(
    match: Match,
    competition: Competition | None,
    *,
    winners: list[tuple[int, int]],
    losers_count: int,
    refunded_count: int,
    total_paid: int,
    cancelled: bool = False,
) -> discord.Embed:
    """Annonce publique du règlement d'un match. ``winners`` = [(discord_id, gain net)]."""
    if cancelled or match.winner is None:
        embed = discord.Embed(
            title=f"🚫 Match annulé : {match.short_title}",
            description=f"{match.title}\nToutes les mises ({refunded_count} pari{'s' if refunded_count > 1 else ''}) ont été **remboursées**.",
            color=Colors.WARNING,
        )
    else:
        winner_name = match.team_name(match.winner)
        score = f" **{match.score_text}**" if match.score_text else ""
        embed = discord.Embed(
            title=f"🏁 Résultat : {match.team_code(1)}{score or ' vs'} {match.team_code(2)}",
            description=f"{Emojis.TROPHY} Victoire de **{winner_name}** !",
            color=Colors.SUCCESS,
        )
        if winners:
            lines = [
                f"{rank_prefix(i)} <@{uid}> {fmt_points(net, signed=True)}"
                for i, (uid, net) in enumerate(winners[:15], start=1)
            ]
            if len(winners) > 15:
                lines.append(f"… et {len(winners) - 15} autre(s) gagnant(s) !")
            embed.add_field(name=f"🎉 Gagnants ({len(winners)})", value="\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="🎉 Gagnants", value="Personne n'avait vu venir ça… 😱", inline=False)
        embed.add_field(name="💰 Points distribués", value=fmt_points(total_paid), inline=True)
        embed.add_field(name="😢 Paris perdus", value=str(losers_count), inline=True)
    context = match_context_line(match, competition)
    if context:
        embed.set_author(name=context[:256])
    embed.set_footer(text="Consulte tes paris avec /pronos mes-paris • Classement : /pronos classement")
    return embed


# ---------------------------------------------------------------------------- classements
def build_leaderboard_embed(
    entries: list[LeaderboardEntry],
    flt: LeaderboardFilter,
    competition: Competition | None,
    *,
    me: tuple[int, int, LeaderboardEntry] | None = None,
    me_id: int | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"{Emojis.TROPHY} Classement des pronostiqueurs",
        description=describe_filter(flt, competition),
        color=Colors.ESPORT,
    )
    if not entries:
        embed.add_field(
            name="Personne pour l'instant",
            value="Aucun pari réglé sur cette période. Lance-toi avec `/pronos matchs` !",
            inline=False,
        )
    else:
        lines = []
        for rank, e in enumerate(entries, start=1):
            rate = f"{e.wins}/{e.bets} gagné{'s' if e.wins > 1 else ''}"
            line = f"{rank_prefix(rank)} <@{e.discord_id}> — **{fmt_points(e.net, signed=True)}** · {rate}"
            if me_id is not None and e.discord_id == me_id:
                line = f"**➜** {line}"
            lines.append(line)
        embed.add_field(name="Gains nets (gains + remboursements − mises)", value="\n".join(lines)[:1024], inline=False)
    if me is not None and me_id is not None:
        rank, total, entry = me
        if rank > len(entries):
            embed.add_field(
                name="📍 Ta position",
                value=f"**{rank}ᵉ** sur {total} — {fmt_points(entry.net, signed=True)} · {entry.wins}/{entry.bets} gagnés",
                inline=False,
            )
    embed.set_footer(text="Égalité départagée au nombre de paris gagnés • /pronos regles")
    return embed


def build_balance_leaderboard_embed(
    entries: list[BalanceEntry], *, me: tuple[int, int] | None = None, me_id: int | None = None,
    my_balance: int | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"{Emojis.COIN} Classement général — les plus riches",
        description="Solde actuel de chaque membre (départ + bonus + gains − mises).",
        color=Colors.INHOUSE,
    )
    if not entries:
        embed.add_field(name="Personne pour l'instant", value="Tape `/pronos solde` pour ouvrir ton portefeuille !", inline=False)
    else:
        lines = []
        for rank, e in enumerate(entries, start=1):
            line = f"{rank_prefix(rank)} <@{e.discord_id}> — **{fmt_points(e.balance)}**"
            if me_id is not None and e.discord_id == me_id:
                line = f"**➜** {line}"
            lines.append(line)
        embed.add_field(name="Top", value="\n".join(lines)[:1024], inline=False)
    if me is not None and my_balance is not None and me[0] > len(entries):
        embed.add_field(name="📍 Ta position", value=f"**{me[0]}ᵉ** sur {me[1]} — {fmt_points(my_balance)}", inline=False)
    return embed


# ---------------------------------------------------------------------------- stats
def build_stats_embed(
    member: discord.abc.User,
    stats: UserStats,
    flt: LeaderboardFilter,
    competition: Competition | None,
    *,
    balance: int,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"{Emojis.CHART} Statistiques de {member.display_name}",
        description=describe_filter(flt, competition),
        color=Colors.ESPORT,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if stats.total == 0:
        embed.add_field(
            name="Aucun pari",
            value="Pas encore de pari sur ce filtre. Les matchs du jour : `/pronos matchs` 🎲",
            inline=False,
        )
        embed.add_field(name="💼 Solde", value=fmt_points(balance), inline=True)
        return embed

    rate = f"{stats.win_rate * 100:.0f} %" if stats.win_rate is not None else "—"
    embed.add_field(name="🎲 Paris", value=f"**{stats.total}** au total\n⏳ {stats.pending} en cours", inline=True)
    embed.add_field(
        name="🎯 Réussite",
        value=f"**{rate}**\n✅ {stats.won} • ❌ {stats.lost}" + (f" • ↩️ {stats.refunded}" if stats.refunded else ""),
        inline=True,
    )
    trend = "📈" if stats.net >= 0 else "📉"
    embed.add_field(
        name=f"{trend} Bilan net",
        value=f"**{fmt_points(stats.net, signed=True)}**\nmisé {fmt_points(stats.staked)} • encaissé {fmt_points(stats.returned)}",
        inline=True,
    )
    embed.add_field(name="💎 Meilleur gain", value=fmt_points(stats.best_gain, signed=True) if stats.best_gain else "—", inline=True)
    if stats.streak_status:
        streak = ("🔥 " if stats.streak_status == "won" else "🥶 ") + (
            f"{stats.streak} victoire{'s' if stats.streak > 1 else ''} d'affilée"
            if stats.streak_status == "won"
            else f"{stats.streak} défaite{'s' if stats.streak > 1 else ''} d'affilée"
        )
    else:
        streak = "—"
    embed.add_field(name="📶 Série en cours", value=streak, inline=True)
    embed.add_field(name="💼 Solde", value=f"{fmt_points(balance)}\n(engagé : {fmt_points(stats.pending_stake)})", inline=True)
    type_lines = []
    for bet_type, ts in sorted(stats.by_type.items()):
        settled = ts.won + ts.lost
        pct = f"{100 * ts.won / settled:.0f} %" if settled else "—"
        type_lines.append(
            f"{bet_type_label(bet_type)} : {ts.bets} pari{'s' if ts.bets > 1 else ''} • {pct} • {fmt_points(ts.net, signed=True)}"
        )
    if type_lines:
        embed.add_field(name="🧩 Par type de pari", value="\n".join(type_lines), inline=False)
    return embed
