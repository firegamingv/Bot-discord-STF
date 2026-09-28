"""``/inhouse inscriptions`` : ouvrir ou fermer les inscriptions d'une session d'inhouse."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.core.checks import organizer_only
from bot.core.errors import UserFacingError
from bot.features.events.registration_toggle import set_registration
from bot.features.inhouse.autocomplete import inhouse_autocomplete, resolve_inhouse
from bot.features.inhouse.group import inhouse_group
from bot.repositories.participants import REGISTERED, ParticipantRepository
from bot.utils import embeds

if TYPE_CHECKING:
    from bot.core.bot import STFBot


@inhouse_group.command(name="inscriptions", description="Ouvrir ou fermer les inscriptions d'un inhouse")
@app_commands.describe(session="La session d'inhouse (tape pour chercher)", etat="Ouvrir ou fermer les inscriptions")
@app_commands.choices(
    etat=[
        app_commands.Choice(name="🔓 Ouvrir", value="ouvrir"),
        app_commands.Choice(name="🔒 Fermer", value="fermer"),
    ]
)
@app_commands.autocomplete(session=inhouse_autocomplete)
@organizer_only()
async def toggle_inhouse_registration(
    interaction: discord.Interaction, session: int, etat: app_commands.Choice[str]
) -> None:
    bot: STFBot = interaction.client  # type: ignore[assignment]
    event, _ = await resolve_inhouse(interaction, session)
    open_ = etat.value == "ouvrir"
    if not event.is_active:
        raise UserFacingError(f"**{event.title}** est terminé : impossible de changer les inscriptions.")
    if open_ and event.has_started:
        raise UserFacingError(
            f"**{event.title}** a déjà commencé : les inscriptions ne peuvent pas être rouvertes. "
            "S'il est reporté, change d'abord sa date avec `/inhouse modifier`."
        )
    if event.registration_open == open_:
        state = "déjà ouvertes 🔓" if open_ else "déjà fermées 🔒"
        await embeds.reply(interaction, embeds.info(f"Les inscriptions à **{event.title}** sont {state}."))
        return

    await set_registration(bot, event, open_)
    if open_:
        text = f"Inscriptions **ouvertes** 🔓 pour **{event.title}** : le bouton « Rejoindre » est actif."
    else:
        count = await ParticipantRepository(bot.db).count(event.id, status=REGISTERED)
        text = (
            f"Inscriptions **fermées** 🔒 pour **{event.title}** ({count} inscrit·e·s).\n"
            "💡 Prochaine étape : `/inhouse equipes-generer` pour constituer les équipes."
        )
    await embeds.reply(interaction, embeds.success(text), ephemeral=True)
