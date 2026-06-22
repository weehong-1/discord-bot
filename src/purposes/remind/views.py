from __future__ import annotations

import discord


class DismissView(discord.ui.View):
    """A persistent view whose single button deletes the message it's attached to.

    Registered once via ``bot.add_view`` so the button keeps working after the
    bot restarts (the reminder message may outlive the process that sent it).
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Dismiss",
        style=discord.ButtonStyle.secondary,
        emoji="✅",
        custom_id="remind:dismiss",
    )
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        message = interaction.message
        if message is None:
            return

        # The reminder mentions its target user; only that user may dismiss it.
        mentioned_ids = {user.id for user in message.mentions}
        if mentioned_ids and interaction.user.id not in mentioned_ids:
            await interaction.response.send_message(
                "Only the person this reminder is for can dismiss it.", ephemeral=True
            )
            return

        try:
            await message.delete()
        except discord.HTTPException:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Couldn't dismiss that reminder.", ephemeral=True
                )
