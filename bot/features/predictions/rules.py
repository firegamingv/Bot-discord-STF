"""``/pronos regles`` : les règles du jeu des pronostics, expliquées simplement."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.features.predictions.embeds import fmt_odds, fmt_points
from bot.features.predictions.group import pronos_group
from bot.services.betting_rules import MIN_STAKE, compute_payout, possible_scores
from bot.utils.embeds import Colors

if TYPE_CHECKING:
    from bot.core.bot import STFBot


async def build_rules_embed(bot: "STFBot", guild_id: int) -> discord.Embed:
    s = await bot.settings.get(guild_id)
    example = compute_payout(100, s.odds_winner)
    embed = discord.Embed(
        title="📜 Règles des pronostics",
        description=(
            "Parie des points virtuels 🪙 sur les matchs de League of Legends esport "
            "(LEC, LCK, Worlds…) et grimpe dans les classements ! Aucun argent réel."
        ),
        color=Colors.ESPORT,
    )
    embed.add_field(
        name="🪙 Tes points",
        value=(
            f"• Capital de départ : **{fmt_points(s.starting_points)}** à ta première commande pronos.\n"
            f"• Bonus quotidien : **{fmt_points(s.daily_points, signed=True)}** au premier passage de la journée "
            "(n'importe quelle commande `/pronos`, ou un clic sur un bouton de pari).\n"
            "• Ton solde = départ + bonus + gains − mises. Consulte-le avec `/pronos solde`."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎲 Parier",
        value=(
            f"• 🏆 **Vainqueur** (tous les matchs) : cote **{fmt_odds(s.odds_winner)}**.\n"
            f"• 🎯 **Score exact** (Bo3/Bo5 seulement) : cote **{fmt_odds(s.odds_exact_score)}** "
            f"— ex. Bo3 : {', '.join(possible_scores(3))}.\n"
            f"• Mise minimale **{MIN_STAKE}** 🪙, maximale : ton solde.\n"
            "• Un pari par type et par match. Tu peux le **modifier** jusqu'au début du match "
            "(l'ancienne mise est remboursée, la nouvelle prélevée).\n"
            "• Les paris ferment **à l'heure de début** du match."
        ),
        inline=False,
    )
    embed.add_field(
        name="💰 Gains",
        value=(
            f"• Gagné : tu reçois **mise × cote** (ex. 100 🪙 en {fmt_odds(s.odds_winner)} → {fmt_points(example)}).\n"
            "• Perdu : la mise est perdue.\n"
            "• Match annulé : mise **remboursée**.\n"
            "• Les résultats sont récupérés automatiquement (toutes les 10 min) et annoncés dans le salon des pronos."
        ),
        inline=False,
    )
    embed.add_field(
        name="🏆 Classements",
        value=(
            "• `/pronos classement` : **gains nets** (gains + remboursements − mises) sur une période "
            "(jour, semaine du lundi, mois, depuis toujours), une compétition, un tournoi ou un type de pari. "
            "Égalité départagée au nombre de paris gagnés.\n"
            "• `/pronos classement-general` : les plus gros **soldes**.\n"
            "• Une mise compte le jour où elle est placée, un gain le jour où le match est réglé."
        ),
        inline=False,
    )
    embed.add_field(
        name="🧭 Commandes utiles",
        value=(
            "`/pronos matchs` • `/pronos parier` • `/pronos parier-score` • `/pronos mes-paris` • "
            "`/pronos solde` • `/pronos stats` • `/pronos classement`"
        ),
        inline=False,
    )
    embed.set_footer(text="Bon jeu et que le meilleur pronostiqueur gagne ! 🍀")
    return embed


@pronos_group.command(name="regles", description="📜 Les règles des pronostics (points, cotes, gains, classements)")
async def rules(interaction: discord.Interaction) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    embed = await build_rules_embed(bot, interaction.guild_id)  # type: ignore[arg-type]
    await interaction.response.send_message(embed=embed, ephemeral=True)
