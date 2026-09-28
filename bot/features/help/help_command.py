"""``/aide`` : guide interactif des commandes, avec un menu de catégories."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.checks import is_organizer
from bot.core.error_reporting import BaseView
from bot.utils.embeds import Colors

if TYPE_CHECKING:
    from bot.core.bot import STFBot

log = logging.getLogger(__name__)

LOCK = "🔒"


@dataclass(frozen=True, slots=True)
class HelpEntry:
    command: str  # nom qualifié, ex. "compte lier"
    description: str
    organizer: bool = False


@dataclass(frozen=True, slots=True)
class HelpCategory:
    key: str
    label: str
    emoji: str
    summary: str
    color: discord.Color
    entries: tuple[HelpEntry, ...]
    tip: str | None = None


CATEGORIES: tuple[HelpCategory, ...] = (
    HelpCategory(
        key="events",
        label="Événements",
        emoji="📅",
        summary="Soirées, tournois, sessions de jeu : inscris-toi en un clic et reçois des rappels.",
        color=Colors.PRIMARY,
        entries=(
            HelpEntry("evenement liste", "Voir les événements à venir sur le serveur."),
            HelpEntry("evenement voir", "Afficher le détail d'un événement (date, places, inscrits)."),
            HelpEntry("evenement rejoindre", "T'inscrire à un événement (liste d'attente s'il est complet)."),
            HelpEntry("evenement quitter", "Te désinscrire ; la première personne en attente prend ta place."),
            HelpEntry("evenement participants", "Voir qui est inscrit et qui est en liste d'attente."),
            HelpEntry("evenement creer", "Créer un événement (titre, date, nombre de places…).", True),
            HelpEntry("evenement modifier", "Changer la date, le titre ou la capacité d'un événement.", True),
            HelpEntry("evenement annoncer", "Publier l'annonce avec les boutons d'inscription.", True),
            HelpEntry("evenement inscriptions", "Ouvrir ou fermer les inscriptions.", True),
            HelpEntry("evenement retirer", "Retirer un participant d'un événement.", True),
            HelpEntry("evenement terminer", "Marquer un événement comme terminé.", True),
            HelpEntry("evenement supprimer", "Supprimer un événement (avec confirmation).", True),
        ),
        tip="Le plus simple : clique sur **S'inscrire** directement sous l'annonce de l'événement !",
    ),
    HelpCategory(
        key="inhouse",
        label="Inhouse",
        emoji="⚔️",
        summary="Parties personnalisées LoL entre membres, avec des équipes équilibrées selon rangs et rôles.",
        color=Colors.INHOUSE,
        entries=(
            HelpEntry("inhouse inscrits", "Voir les inscrits d'un inhouse avec leur rang et leurs rôles."),
            HelpEntry("inhouse creer", "Créer un inhouse (Faille, ARAM, Arena…).", True),
            HelpEntry("inhouse modifier", "Modifier un inhouse existant.", True),
            HelpEntry("inhouse inscriptions", "Ouvrir ou fermer les inscriptions de l'inhouse.", True),
            HelpEntry("inhouse annoncer", "Publier l'annonce de l'inhouse avec ses boutons.", True),
            HelpEntry("inhouse equipes-generer", "Générer des équipes équilibrées automatiquement.", True),
            HelpEntry("inhouse equipes-echanger", "Échanger deux joueurs entre les équipes.", True),
            HelpEntry("inhouse equipes-deplacer", "Déplacer un joueur dans une autre équipe.", True),
            HelpEntry("inhouse equipes-publier", "Publier les équipes finales dans le salon.", True),
        ),
        tip="Lie ton compte LoL (`/compte lier`) et choisis tes rôles pour que les équipes soient justes.",
    ),
    HelpCategory(
        key="account",
        label="Compte LoL",
        emoji="🎮",
        summary="Relie ton compte League of Legends pour afficher ton rang et équilibrer les inhouses.",
        color=Colors.SUCCESS,
        entries=(
            HelpEntry("compte lier", "Lier ton Riot ID (`Pseudo#TAG`) à ton compte Discord."),
            HelpEntry("compte roles", "Choisir ton rôle principal et tes rôles secondaires."),
            HelpEntry("compte profil", "Voir ton profil (ou celui d'un membre) : rang, rôles, op.gg."),
            HelpEntry("compte actualiser", "Mettre à jour ton rang et ton Riot ID depuis les serveurs Riot."),
            HelpEntry("compte delier", "Délier ton compte (avec confirmation)."),
        ),
        tip="Ton Riot ID est visible en haut à droite du client LoL, en survolant ton pseudo.",
    ),
    HelpCategory(
        key="predictions",
        label="Pronostics",
        emoji="🎯",
        summary="Parie des points (fictifs !) sur les matchs esport et grimpe au classement.",
        color=Colors.ESPORT,
        entries=(
            HelpEntry("pronos matchs", "Voir les prochains matchs sur lesquels parier."),
            HelpEntry("pronos parier", "Parier sur le vainqueur d'un match."),
            HelpEntry("pronos parier-score", "Parier sur le score exact d'un Bo3 / Bo5 (grosse cote !)."),
            HelpEntry("pronos mes-paris", "Voir tes paris en cours et passés."),
            HelpEntry("pronos solde", "Voir ton solde de points."),
            HelpEntry("pronos stats", "Tes statistiques de pronostics (réussite, gains…)."),
            HelpEntry("pronos classement", "Le classement des pronostiqueurs (période, compétition…)."),
            HelpEntry("pronos classement-general", "Le classement général par solde de points."),
            HelpEntry("pronos regles", "Comment fonctionnent les points, les cotes et les paris."),
            HelpEntry("pronos-admin competitions", "Choisir les compétitions suivies.", True),
            HelpEntry("pronos-admin synchroniser", "Récupérer les matchs depuis LoL Esports.", True),
            HelpEntry("pronos-admin competition-creer", "Créer une compétition maison (matchs manuels).", True),
            HelpEntry("pronos-admin match-ajouter", "Ajouter un match manuellement.", True),
            HelpEntry("pronos-admin resultat", "Saisir le résultat d'un match et payer les paris.", True),
            HelpEntry("pronos-admin annuler-match", "Annuler un match et rembourser les mises.", True),
            HelpEntry("pronos-admin classement-auto", "Publier un classement automatiquement.", True),
            HelpEntry("pronos-admin points", "Ajouter ou retirer des points à un membre.", True),
        ),
        tip="Pas de vrai argent ici : uniquement des points pour la gloire 🏆",
    ),
    HelpCategory(
        key="config",
        label="Configuration",
        emoji="⚙️",
        summary="Réglages du bot sur ce serveur (réservé aux organisateurs).",
        color=Colors.NEUTRAL,
        entries=(
            HelpEntry("config voir", "Récapitulatif de tous les réglages, avec conseils.", True),
            HelpEntry("config salon-annonces", "Salon des annonces, rappels et équipes d'inhouse.", True),
            HelpEntry("config salon-pronos", "Salon des matchs du jour et des classements.", True),
            HelpEntry("config role-organisateur", "Rôle autorisé à gérer événements et pronos.", True),
            HelpEntry("config rappels", "Quand rappeler les inscrits (ex. `1j, 1h, 15min`).", True),
            HelpEntry("config bareme-pronos", "Points quotidiens, points de départ et cotes.", True),
        ),
        tip="Nouveau serveur ? Commence par `/config voir` : je te dis ce qu'il reste à régler.",
    ),
)
CATEGORY_BY_KEY = {c.key: c for c in CATEGORIES}
HOME_KEY = "home"


# ---------------------------------------------------------------------- mentions cliquables
_MENTION_TTL = 3600.0
_mention_cache: dict[int, tuple[float, dict[str, str]]] = {}


async def _command_mentions(bot: "STFBot", guild: discord.abc.Snowflake | None) -> dict[str, str]:
    """Nom qualifié -> mention cliquable (``</compte lier:123>``). Vide en cas d'échec."""
    key = guild.id if guild else 0
    cached = _mention_cache.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    mentions: dict[str, str] = {}
    try:
        # Délai court : on doit répondre à l'interaction en moins de 3 secondes.
        commands = await asyncio.wait_for(bot.tree.fetch_commands(guild=guild), 1.2) if guild else []
        if not commands:
            commands = await asyncio.wait_for(bot.tree.fetch_commands(), 1.2)
        for cmd in commands:
            mentions[cmd.name] = cmd.mention
            for opt in cmd.options:
                if isinstance(opt, app_commands.AppCommandGroup):
                    mentions[f"{cmd.name} {opt.name}"] = opt.mention
                    for sub in opt.options:
                        if isinstance(sub, app_commands.AppCommandGroup):
                            mentions[f"{cmd.name} {opt.name} {sub.name}"] = sub.mention
    except (discord.HTTPException, asyncio.TimeoutError):
        log.debug("Impossible de récupérer les commandes pour les mentions de /aide", exc_info=True)
    _mention_cache[key] = (time.monotonic() + (_MENTION_TTL if mentions else 60.0), mentions)
    return mentions


def _fmt(command: str, mentions: dict[str, str]) -> str:
    return mentions.get(command) or f"`/{command}`"


# ---------------------------------------------------------------------- embeds
def build_home_embed(bot_user: discord.abc.User | None, *, organizer: bool, mentions: dict[str, str]) -> discord.Embed:
    name = bot_user.display_name if bot_user else "le bot"
    embed = discord.Embed(
        title=f"👋 Salut ! Je suis {name}",
        description=(
            "J'organise vos **événements**, vos **inhouses League of Legends** et un jeu de "
            "**pronostics esport**. Choisis une catégorie dans le menu ci-dessous pour voir les commandes."
        ),
        color=Colors.PRIMARY,
    )
    embed.add_field(
        name="🚀 Bien démarrer",
        value=(
            f"**1.** Lie ton compte LoL : {_fmt('compte lier', mentions)}\n"
            f"**2.** Choisis tes rôles : {_fmt('compte roles', mentions)}\n"
            f"**3.** Inscris-toi aux événements : bouton **S'inscrire** sous les annonces, "
            f"ou {_fmt('evenement liste', mentions)}\n"
            f"**4.** Tente tes pronos : {_fmt('pronos matchs', mentions)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="📚 Catégories",
        value="\n".join(f"{c.emoji} **{c.label}** — {c.summary}" for c in CATEGORIES),
        inline=False,
    )
    if organizer:
        embed.add_field(
            name="🛠️ Tu es organisateur",
            value=f"Vérifie la configuration du serveur avec {_fmt('config voir', mentions)}.",
            inline=False,
        )
    embed.set_footer(text=f"{LOCK} = réservé aux organisateurs • Les réponses du bot ne sont visibles que par toi")
    return embed


def build_category_embed(category: HelpCategory, *, organizer: bool, mentions: dict[str, str]) -> discord.Embed:
    embed = discord.Embed(
        title=f"{category.emoji} {category.label}", description=category.summary, color=category.color
    )
    members = [e for e in category.entries if not e.organizer]
    staff = [e for e in category.entries if e.organizer]
    if members:
        embed.add_field(
            name="Pour tout le monde",
            value="\n".join(f"{_fmt(e.command, mentions)} — {e.description}" for e in members)[:1024],
            inline=False,
        )
    if staff:
        title = f"{LOCK} Organisateurs" + ("" if organizer else " (tu n'as pas accès à ces commandes)")
        embed.add_field(
            name=title,
            value="\n".join(f"{_fmt(e.command, mentions)} — {e.description}" for e in staff)[:1024],
            inline=False,
        )
    if category.tip:
        embed.add_field(name="💡 Astuce", value=category.tip, inline=False)
    embed.set_footer(text="Astuce : tape / puis le nom d'une commande, les options sont proposées automatiquement")
    return embed


# ---------------------------------------------------------------------- vue
class HelpView(BaseView):
    def __init__(self, *, user_id: int, organizer: bool, mentions: dict[str, str], bot_user) -> None:
        super().__init__(timeout=600)
        self.user_id = user_id
        self.organizer = organizer
        self.mentions = mentions
        self.bot_user = bot_user
        self.origin: discord.Interaction | None = None
        options = [discord.SelectOption(label="Accueil", value=HOME_KEY, emoji="🏠", default=True)]
        options += [
            discord.SelectOption(label=c.label, value=c.key, emoji=c.emoji, description=c.summary[:100])
            for c in CATEGORIES
        ]
        self.menu: discord.ui.Select = discord.ui.Select(placeholder="📚 Choisis une catégorie…", options=options)
        self.menu.callback = self._on_select  # type: ignore[method-assign]
        self.add_item(self.menu)

    def embed_for(self, key: str) -> discord.Embed:
        if key == HOME_KEY or key not in CATEGORY_BY_KEY:
            return build_home_embed(self.bot_user, organizer=self.organizer, mentions=self.mentions)
        return build_category_embed(CATEGORY_BY_KEY[key], organizer=self.organizer, mentions=self.mentions)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def _on_select(self, interaction: discord.Interaction) -> None:
        key = self.menu.values[0]
        for opt in self.menu.options:
            opt.default = opt.value == key
        await interaction.response.edit_message(embed=self.embed_for(key), view=self)

    async def on_timeout(self) -> None:
        self.menu.disabled = True
        if self.origin is not None:
            try:
                await self.origin.edit_original_response(view=self)
            except discord.HTTPException:
                pass


@app_commands.command(name="aide", description="Découvrir les commandes du bot et comment bien démarrer")
@app_commands.describe(categorie="Aller directement à une catégorie")
@app_commands.choices(
    categorie=[app_commands.Choice(name=f"{c.emoji} {c.label}", value=c.key) for c in CATEGORIES]
)
async def help_command(interaction: discord.Interaction, categorie: app_commands.Choice[str] | None = None) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    # La récupération des mentions de commandes (API Discord) peut prendre jusqu'à ~2,5 s.
    await interaction.response.defer(ephemeral=True, thinking=True)
    organizer = await is_organizer(interaction) if interaction.guild else False
    mentions = await _command_mentions(bot, interaction.guild)
    view = HelpView(user_id=interaction.user.id, organizer=organizer, mentions=mentions, bot_user=bot.user)
    key = categorie.value if categorie else HOME_KEY
    for opt in view.menu.options:
        opt.default = opt.value == key
    await interaction.edit_original_response(embed=view.embed_for(key), view=view)
    view.origin = interaction
