from __future__ import annotations

import io
import logging

import discord
from discord import app_commands

from ...config import Settings
from ...services.ai_client import AIClient, AIProviderError
from .formatters import FORMATTER_CHOICES, extension_for, format_code

LOGGER = logging.getLogger(__name__)

DISCORD_MESSAGE_LIMIT = 2000


def register_commands(
    tree: app_commands.CommandTree[discord.Client],
    settings: Settings,
    ai_client: AIClient,
) -> None:
    @tree.command(name="code", description="Format pasted code and wrap it in a Discord code block.")
    @app_commands.describe(language="Language to format as. Leave blank to auto-detect.")
    @app_commands.choices(language=FORMATTER_CHOICES)
    async def code(
        interaction: discord.Interaction,
        language: app_commands.Choice[str] | None = None,
    ) -> None:
        if not await _ensure_allowed_guild(interaction, settings):
            return

        await interaction.response.send_modal(
            CodeBlockModal(ai_client, language.value if language else None)
        )


class CodeBlockModal(discord.ui.Modal, title="Format Code"):
    content = discord.ui.TextInput(
        label="Code",
        placeholder="Paste your code here",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
    )

    def __init__(self, ai_client: AIClient, language: str | None) -> None:
        super().__init__()
        self._ai_client = ai_client
        self._language = language

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)

        try:
            formatted, tag = await format_code(str(self.content), self._language, self._ai_client)
        except AIProviderError as exc:
            await interaction.followup.send(f"I could not format that code: {exc}")
            return
        except Exception:
            LOGGER.exception("Unexpected error while formatting /code")
            await interaction.followup.send("Unexpected error while formatting. Check the bot logs for details.")
            return

        # Each message is "```{tag}\n{body}\n```", so the wrapper costs
        # len("```") + len(tag) + two newlines + len("```") == 8 + len(tag).
        safe = formatted.replace("```", "`​``")
        if len(safe) + 8 + len(tag) <= DISCORD_MESSAGE_LIMIT:
            # Fits in a single message: send it inline, never chopped.
            await interaction.followup.send(f"```{tag}\n{safe}\n```")
            return

        # Too big for one message. Attach the unescaped code as a file so it
        # arrives whole instead of being split across several code blocks.
        buffer = io.BytesIO(formatted.encode("utf-8"))
        file = discord.File(buffer, filename=f"formatted.{extension_for(tag)}")
        await interaction.followup.send(
            f"Formatted `{tag}` ({len(formatted)} chars) — too long for one message, attached as a file:",
            file=file,
        )


async def _ensure_allowed_guild(interaction: discord.Interaction, settings: Settings) -> bool:
    if interaction.guild_id != settings.discord_guild_id:
        await interaction.response.send_message("This bot is not enabled in this server.", ephemeral=True)
        return False
    return True
