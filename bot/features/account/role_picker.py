"""Sélecteur de rôles League of Legends (rôle principal + rôles secondaires).

API publique :
- ``send_role_picker(interaction, *, after_save=None)`` : ouvre le sélecteur (éphémère).
  ``after_save(interaction, roles)`` est appelé après l'enregistrement ; l'interaction a déjà
  reçu une réponse (message édité) : utiliser ``interaction.followup`` dans ce callback.
- ``RolesButton`` : bouton persistant ``acc:roles`` qui ouvre le sélecteur.

Discord ne garantit pas l'ordre des valeurs d'un menu à choix multiples : on sépare donc
le **rôle principal** (1 choix) des **rôles secondaires** (0 à 4, rangés dans l'ordre
canonique Top → Support, « Fill » toujours en dernier).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import discord

from bot.core.error_reporting import BaseView, interaction_guard
from bot.core.errors import UserFacingError
from bot.repositories.player_roles import LOL_ROLE_EMOJIS, LOL_ROLES, PlayerRoleRepository, role_label
from bot.utils.embeds import Colors

log = logging.getLogger(__name__)

AfterSave = Callable[[discord.Interaction, list[str]], Awaitable[None]]
MAX_SECONDARY = 4
PICKER_TIMEOUT = 300  # secondes


def order_roles(main: str | None, secondary: list[str]) -> list[str]:
    """Rôle principal d'abord, puis secondaires dans l'ordre canonique, « fill » en dernier."""
    canonical = list(LOL_ROLES)
    result: list[str] = [main] if main else []
    extras = sorted(
        {r for r in secondary if r in LOL_ROLES and r != main},
        key=lambda r: (r == "fill", canonical.index(r)),
    )
    return result + extras


def _options(selected: set[str]) -> list[discord.SelectOption]:
    return [
        discord.SelectOption(label=label, value=key, emoji=LOL_ROLE_EMOJIS.get(key), default=key in selected)
        for key, label in LOL_ROLES.items()
    ]


def build_picker_embed(main: str | None, secondary: list[str], *, saved: bool = False) -> discord.Embed:
    roles = order_roles(main, secondary)
    if saved:
        embed = discord.Embed(title="✅ Rôles enregistrés !", color=Colors.SUCCESS)
        embed.description = (
            "Ils serviront à composer des équipes équilibrées lors des inhouses. "
            "Tu peux les changer à tout moment avec `/compte roles`."
        )
    else:
        embed = discord.Embed(title="🎮 Choisis tes rôles", color=Colors.INHOUSE)
        embed.description = (
            "1️⃣ Choisis ton **rôle principal** dans le premier menu.\n"
            f"2️⃣ Ajoute jusqu'à **{MAX_SECONDARY} rôles secondaires** si tu es flexible (facultatif).\n"
            "3️⃣ Clique sur **Enregistrer**.\n\n"
            "-# 🎲 *Fill* = « peu importe, je joue où il faut »."
        )
    preview = "\n".join(
        f"{'⭐' if i == 0 else '▫️'} **{i + 1}.** {role_label(r)}" for i, r in enumerate(roles)
    ) or "*Rien de sélectionné pour l'instant*"
    embed.add_field(name="Ordre de préférence", value=preview, inline=False)
    return embed


class RolePickerView(BaseView):
    def __init__(
        self,
        user_id: int,
        current_roles: list[str],
        *,
        after_save: AfterSave | None = None,
        timeout: float = PICKER_TIMEOUT,
    ) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.after_save = after_save
        self.main: str | None = current_roles[0] if current_roles else None
        self.secondary: list[str] = [r for r in current_roles[1:] if r != self.main][:MAX_SECONDARY]
        self.message: discord.Message | discord.WebhookMessage | None = None
        self.origin: discord.Interaction | None = None

        self.main_select: discord.ui.Select = discord.ui.Select(
            placeholder="⭐ Rôle principal",
            min_values=1,
            max_values=1,
            options=_options({self.main} if self.main else set()),
            row=0,
        )
        self.main_select.callback = self._on_main  # type: ignore[method-assign]
        self.add_item(self.main_select)

        self.secondary_select: discord.ui.Select = discord.ui.Select(
            placeholder=f"Rôles secondaires (0 à {MAX_SECONDARY}, facultatif)",
            min_values=0,
            max_values=MAX_SECONDARY,
            options=_options(set(self.secondary)),
            row=1,
        )
        self.secondary_select.callback = self._on_secondary  # type: ignore[method-assign]
        self.add_item(self.secondary_select)

    # ------------------------------------------------------------------ garde-fous
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Ce sélecteur n'est pas le tien : utilise `/compte roles` pour choisir tes rôles.",
                ephemeral=True,
            )
            return False
        return True

    def _refresh_defaults(self) -> None:
        self.main_select.options = _options({self.main} if self.main else set())
        self.secondary_select.options = _options(set(self.secondary))

    # ------------------------------------------------------------------ callbacks
    async def _on_main(self, interaction: discord.Interaction) -> None:
        self.main = self.main_select.values[0]
        self.secondary = [r for r in self.secondary if r != self.main]
        self._refresh_defaults()
        await interaction.response.edit_message(embed=build_picker_embed(self.main, self.secondary), view=self)

    async def _on_secondary(self, interaction: discord.Interaction) -> None:
        self.secondary = [r for r in self.secondary_select.values if r != self.main]
        self._refresh_defaults()
        await interaction.response.edit_message(embed=build_picker_embed(self.main, self.secondary), view=self)

    @discord.ui.button(label="Enregistrer", emoji="💾", style=discord.ButtonStyle.success, row=2)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.main:
            raise UserFacingError("Choisis d'abord ton **rôle principal** dans le premier menu.")
        roles = order_roles(self.main, self.secondary)
        bot = interaction.client
        await PlayerRoleRepository(bot.db).set(interaction.user.id, roles)  # type: ignore[attr-defined]
        log.info("Rôles de %s (%s) : %s", interaction.user, interaction.user.id, roles)
        self.stop()
        await interaction.response.edit_message(
            embed=build_picker_embed(self.main, self.secondary, saved=True), view=None
        )
        if self.after_save is not None:
            await self.after_save(interaction, roles)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary, row=2)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(description="Aucun changement : tes rôles n'ont pas été modifiés.",
                                color=Colors.NEUTRAL),
            view=None,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        embed = build_picker_embed(self.main, self.secondary)
        embed.set_footer(text="⌛ Sélecteur expiré — relance /compte roles pour modifier tes rôles.")
        try:
            if self.origin is not None:
                await self.origin.edit_original_response(embed=embed, view=self)
            elif self.message is not None:
                await self.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            pass


async def send_role_picker(interaction: discord.Interaction, *, after_save: AfterSave | None = None) -> None:
    """Ouvre le sélecteur de rôles en message éphémère (répond ou envoie un followup)."""
    bot = interaction.client
    current = await PlayerRoleRepository(bot.db).get(interaction.user.id)  # type: ignore[attr-defined]
    view = RolePickerView(interaction.user.id, current, after_save=after_save)
    embed = build_picker_embed(view.main, view.secondary)
    if interaction.response.is_done():
        view.message = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.origin = interaction


class RolesButton(discord.ui.DynamicItem[discord.ui.Button], template=r"acc:roles"):
    """Bouton persistant « Mes rôles » (``acc:roles``) : ouvre le sélecteur de rôles."""

    def __init__(self, *, label: str = "Choisir mes rôles", row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=label, emoji="🎮", style=discord.ButtonStyle.secondary, custom_id="acc:roles", row=row
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match, /
    ) -> "RolesButton":
        return cls(label=item.label or "Choisir mes rôles")

    async def callback(self, interaction: discord.Interaction) -> None:
        async with interaction_guard(interaction):
            await send_role_picker(interaction)
