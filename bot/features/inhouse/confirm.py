"""Petite vue de confirmation Oui / Non réutilisable par les commandes d'inhouse."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord

from bot.core.error_reporting import BaseView
from bot.utils import embeds

OnConfirm = Callable[[discord.Interaction], Awaitable[None]]


class ConfirmView(BaseView):
    """Boutons « Oui » / « Non », utilisables uniquement par l'auteur de la commande.

    ``on_confirm(interaction)`` est appelé après un clic sur « Oui » (l'interaction n'a pas
    encore reçu de réponse).
    """

    def __init__(
        self,
        author_id: int,
        on_confirm: OnConfirm,
        *,
        confirm_label: str = "Oui, continuer",
        cancel_text: str = "Action annulée, rien n'a changé.",
        timeout: float = 120,
    ) -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.on_confirm = on_confirm
        self.cancel_text = cancel_text
        self.confirm.label = confirm_label
        self.message: discord.InteractionMessage | discord.WebhookMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=embeds.error("Seule la personne qui a lancé la commande peut confirmer."), ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(embed=embeds.info(f"⌛ Temps écoulé : {self.cancel_text}"), view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Oui, continuer", emoji="✅", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await self.on_confirm(interaction)

    @discord.ui.button(label="Non", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(embed=embeds.info(self.cancel_text), view=None)
