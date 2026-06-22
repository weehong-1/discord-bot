from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands

from ...config import Settings


LOGGER = logging.getLogger(__name__)
MAX_TIMER_MINUTES = 24 * 60


def register_commands(tree: app_commands.CommandTree[discord.Client], settings: Settings) -> None:
    @tree.command(name="timer", description="Create a countdown timer with a start button.")
    @app_commands.describe(minutes="Timer length in minutes, from 1 to 1440")
    async def timer(interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 1440]) -> None:
        if not await _ensure_allowed_guild(interaction, settings):
            return

        view = TimerStartView(minutes=minutes, owner_id=interaction.user.id)
        await interaction.response.send_message(
            f"Timer ready for `{minutes}` minute{'s' if minutes != 1 else ''}. Click Start when you are ready.",
            view=view,
        )


class TimerStartView(discord.ui.View):
    def __init__(self, minutes: int, owner_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.minutes = minutes
        self.owner_id = owner_id
        self.started = False
        self.task: asyncio.Task[None] | None = None

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button["TimerStartView"]) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the timer creator can start this timer.", ephemeral=True)
            return

        if self.started:
            await interaction.response.send_message("This timer has already started.", ephemeral=True)
            return

        self.started = True
        button.disabled = True
        button.label = "Started"
        self.task = asyncio.create_task(_finish_timer(interaction, self.minutes))
        await interaction.response.edit_message(
            content=(
                f"Timer started for `{self.minutes}` minute{'s' if self.minutes != 1 else ''}. "
                f"I will ping {interaction.user.mention} when it ends."
            ),
            view=self,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button["TimerStartView"]) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the timer creator can cancel this timer.", ephemeral=True)
            return

        if self.task and not self.task.done():
            self.task.cancel()

        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(
            content=f"Timer cancelled by {interaction.user.mention}.",
            view=self,
        )


async def _finish_timer(interaction: discord.Interaction, minutes: int) -> None:
    try:
        await asyncio.sleep(minutes * 60)
    except asyncio.CancelledError:
        return

    try:
        await interaction.channel.send(  # type: ignore[union-attr]
            f"{interaction.user.mention} timer done: `{minutes}` minute{'s' if minutes != 1 else ''} finished.",
            allowed_mentions=discord.AllowedMentions(users=True),
        )
    except Exception:
        LOGGER.exception("Failed to send timer completion message")


async def _ensure_allowed_guild(interaction: discord.Interaction, settings: Settings) -> bool:
    if interaction.guild_id != settings.discord_guild_id:
        await interaction.response.send_message("This bot is not enabled in this server.", ephemeral=True)
        return False
    return True
