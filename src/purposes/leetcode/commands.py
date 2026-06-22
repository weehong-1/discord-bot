from __future__ import annotations

import logging
import re

import discord
from discord import app_commands

from ...config import Settings
from ...services.ai_client import AIClient, AIProviderError
from .leetcode_client import LeetCodeClient, LeetCodeError
from .models import MODEL_ALLOWLIST, MODEL_CHOICES
from .tutor import build_messages, build_pseudocode_messages, chunk_for_discord


LOGGER = logging.getLogger(__name__)

LEETCODE_SIGNALS = (
    "leetcode",
    "algorithm",
    "data structure",
    "complexity",
    "dp",
    "dynamic programming",
    "greedy",
    "bfs",
    "dfs",
    "binary search",
    "two pointers",
    "sliding window",
    "linked list",
    "tree",
    "graph",
    "stack",
    "queue",
    "heap",
)


def register_commands(
    tree: app_commands.CommandTree[discord.Client],
    settings: Settings,
    ai_client: AIClient,
    leetcode_client: LeetCodeClient,
) -> None:
    @tree.command(name="models", description="Show allowed OpenRouter model choices.")
    async def models(interaction: discord.Interaction) -> None:
        if not await _ensure_allowed_guild(interaction, settings):
            return

        lines = [f"Default: `{settings.openrouter_model_key}` -> `{settings.openrouter_model_id}`", "", "Allowed models:"]
        lines.extend(f"- `{key}` -> `{model_id}`" for key, model_id in MODEL_ALLOWLIST.items())
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @tree.command(name="leetcode", description="Start a tutoring thread for a LeetCode problem.")
    @app_commands.describe(problem="LeetCode number, slug, or URL, e.g. 2314, two-sum, or a problem URL")
    async def leetcode(interaction: discord.Interaction, problem: str) -> None:
        if not await _ensure_allowed_guild(interaction, settings):
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "Run `/leetcode` from a normal text channel so I can open a thread.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            info = await leetcode_client.resolve_problem(problem)
        except LeetCodeError as exc:
            await interaction.followup.send(f"I could not find that LeetCode problem: {exc}")
            return
        except Exception:
            LOGGER.exception("Unexpected error while resolving /leetcode problem")
            await interaction.followup.send("Unexpected error while looking up the problem. Check the bot logs for details.")
            return

        if interaction.guild is not None:
            existing = await _find_problem_thread(interaction.guild, interaction.channel, info.frontend_id)
            if existing is not None:
                await interaction.followup.send(
                    f"There is already a tutoring thread for `{info.frontend_id}. {info.title}` → {existing.mention}",
                    ephemeral=True,
                )
                return

        url = f"https://leetcode.com/problems/{info.slug}/"
        thread = await interaction.channel.create_thread(
            name=f"{info.frontend_id} - {info.title}"[:100],
            type=discord.ChannelType.public_thread,
        )
        try:
            await thread.add_user(interaction.user)
        except discord.HTTPException:
            LOGGER.exception("Could not add /leetcode user to created thread")

        await interaction.followup.send(
            f"🧩 Tutoring thread started for `{info.frontend_id}. {info.title}` → {thread.mention}",
            ephemeral=True,
        )
        await thread.send(
            f"{info.frontend_id}. {info.title}\n"
            f"Difficulty: {info.difficulty}\n"
            f"Link: {url}"
        )

    @tree.command(
        name="delete",
        description="Delete a LeetCode tutoring thread and its announcement message by problem number.",
    )
    @app_commands.describe(problem_number="LeetCode problem number, e.g. 1344")
    async def delete(interaction: discord.Interaction, problem_number: str) -> None:
        if not await _ensure_allowed_guild(interaction, settings):
            return

        number = problem_number.strip()
        if not number.isdigit():
            await interaction.response.send_message(
                "Give me just the LeetCode problem number, e.g. `1344`.",
                ephemeral=True,
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message("Run `/delete` inside the server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        thread = await _find_problem_thread(interaction.guild, interaction.channel, number)
        if thread is None:
            await interaction.followup.send(
                f"I could not find a tutoring thread for problem `{number}`."
            )
            return

        bot_user = interaction.client.user
        thread_name = thread.name
        deleted_message = False
        deleted_notice = False
        if isinstance(thread.parent, discord.TextChannel):
            try:
                async for message in thread.parent.history(limit=200):
                    is_announcement = (
                        bot_user is not None
                        and message.author.id == bot_user.id
                        and f"<#{thread.id}>" in message.content
                    )
                    # Discord's auto-posted "X started a thread: <name>" system message.
                    is_notice = (
                        message.type is discord.MessageType.thread_created
                        and message.content == thread_name
                    )
                    if is_announcement or is_notice:
                        await message.delete()
                        deleted_message = deleted_message or is_announcement
                        deleted_notice = deleted_notice or is_notice
                    if deleted_message and deleted_notice:
                        break
            except discord.HTTPException:
                LOGGER.exception("Failed while deleting /leetcode channel messages")

        try:
            await thread.delete()
        except discord.HTTPException as exc:
            await interaction.followup.send(f"I found the thread but could not delete it: {exc}")
            return

        summary = f"Deleted thread `{thread_name}`."
        summary += (
            " Removed its announcement message."
            if deleted_message
            else " No announcement message found to remove."
        )
        summary += (
            " Removed the thread-created notice."
            if deleted_notice
            else " No thread-created notice found to remove."
        )
        await interaction.followup.send(summary)

    @tree.command(name="pseudocode", description="Format raw algorithm notes for Discord.")
    @app_commands.describe(raw_list="Paste your raw algorithm or process list here")
    async def pseudocode(interaction: discord.Interaction, raw_list: str) -> None:
        if not await _ensure_allowed_guild(interaction, settings):
            return

        await interaction.response.defer(thinking=True)

        try:
            answer = await ai_client.complete(build_pseudocode_messages(raw_list))
        except AIProviderError as exc:
            await interaction.followup.send(f"I could not format the pseudocode: {exc}")
            return
        except Exception:
            LOGGER.exception("Unexpected error while formatting /pseudocode")
            await interaction.followup.send("Unexpected error while formatting. Check the bot logs for details.")
            return

        chunks = chunk_for_discord(_clean_pseudocode_response(answer.content))
        await interaction.followup.send(chunks[0])
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk)


async def answer_mention(message: discord.Message, ai_client: AIClient, question: str, context: str = "") -> None:
    async with message.channel.typing():
        messages = build_messages(question, context=context)
        try:
            answer = await ai_client.complete(messages)
        except AIProviderError as exc:
            await message.reply(f"I could not get an AI response: {exc}", mention_author=False)
            return
        except Exception:
            LOGGER.exception("Unexpected error while answering LeetCode mention")
            await message.reply("Unexpected error while answering. Check the bot logs for details.", mention_author=False)
            return

    prefix = f"Model: `{answer.provider}` / `{answer.model}`"
    if answer.used_fallback:
        prefix += "\nOpenRouter failed, answered with DeepSeek fallback."

    chunks = chunk_for_discord(answer.content)
    await message.reply(f"{prefix}\n\n{chunks[0]}", mention_author=False)
    for chunk in chunks[1:]:
        await message.channel.send(chunk)


def looks_like_leetcode(text: str, channel_name: str | None = None) -> bool:
    haystack = f"{channel_name or ''} {text}".lower()
    if "leetcode.com/problems/" in haystack:
        return True
    if any(signal in haystack for signal in LEETCODE_SIGNALS):
        return True
    return bool(re.search(r"\bproblem\s*#?\d+\b|\bquestion\s*#?\d+\b", haystack))


async def _find_problem_thread(
    guild: discord.Guild,
    channel: discord.abc.GuildChannel | discord.Thread | discord.abc.PrivateChannel | None,
    number: str,
) -> discord.Thread | None:
    prefix = f"{number} - "

    for thread in await guild.active_threads():
        if thread.name.startswith(prefix):
            return thread

    if isinstance(channel, discord.TextChannel):
        async for thread in channel.archived_threads(limit=100):
            if thread.name.startswith(prefix):
                return thread

    return None


def _clean_pseudocode_response(content: str) -> str:
    lines = [line for line in content.replace("\\`", "`").splitlines() if line.strip() not in {"---", "***"}]
    return "\n".join(lines).strip()


async def _ensure_allowed_guild(interaction: discord.Interaction, settings: Settings) -> bool:
    if interaction.guild_id != settings.discord_guild_id:
        await interaction.response.send_message("This bot is not enabled in this server.", ephemeral=True)
        return False
    if not is_channel_in_scope(interaction.channel, settings):
        await interaction.response.send_message(
            f"This bot only works in <#{settings.discord_channel_id}>.",
            ephemeral=True,
        )
        return False
    return True


def is_channel_in_scope(
    channel: discord.abc.GuildChannel | discord.Thread | discord.abc.PrivateChannel | None,
    settings: Settings,
) -> bool:
    """Whether `channel` is the configured channel or a thread under it.

    When no channel is configured the bot is unrestricted.
    """
    if settings.discord_channel_id is None:
        return True
    if channel is None:
        return False
    if channel.id == settings.discord_channel_id:
        return True
    return isinstance(channel, discord.Thread) and channel.parent_id == settings.discord_channel_id
