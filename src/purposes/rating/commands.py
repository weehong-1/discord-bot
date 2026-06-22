from __future__ import annotations

import logging

import discord
from discord import app_commands

from ...config import Settings
from ...services.ai_client import AIClient, AIProviderError
from .engine import (
    ReviewParseError,
    ReviewResult,
    build_review_messages,
    compute_delta,
    parse_review,
    score_of,
    tier_for,
)
from .store import RatingStore, UserRating

LOGGER = logging.getLogger(__name__)


def register_commands(
    tree: app_commands.CommandTree[discord.Client],
    settings: Settings,
    ai_client: AIClient,
    rating_store: RatingStore,
) -> None:
    @tree.command(name="submit", description="Submit code for a rated review and see your rating change.")
    async def submit(interaction: discord.Interaction) -> None:
        if not await _ensure_allowed_guild(interaction, settings):
            return
        await interaction.response.send_modal(SubmitModal(ai_client, rating_store))

    @tree.command(name="rating", description="Show your personal code-review rating and stats.")
    async def rating(interaction: discord.Interaction) -> None:
        if not await _ensure_allowed_guild(interaction, settings):
            return

        record = rating_store.get(interaction.user.id)
        await interaction.response.send_message(
            embed=_rating_embed(interaction.user, record),
            ephemeral=True,
        )


class SubmitModal(discord.ui.Modal, title="Submit code for review"):
    code = discord.ui.TextInput(
        label="Code",
        placeholder="Paste the source code you want reviewed and rated",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
    )

    def __init__(self, ai_client: AIClient, rating_store: RatingStore) -> None:
        super().__init__()
        self._ai_client = ai_client
        self._rating_store = rating_store

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)

        try:
            answer = await self._ai_client.complete(build_review_messages(str(self.code)))
            result = parse_review(answer.content)
        except AIProviderError as exc:
            await interaction.followup.send(f"I could not review that submission: {exc}")
            return
        except ReviewParseError:
            LOGGER.warning("Could not parse review response for /submit", exc_info=True)
            await interaction.followup.send(
                "The reviewer returned something I could not score. Your rating is unchanged — please try again."
            )
            return
        except Exception:
            LOGGER.exception("Unexpected error while handling /submit")
            await interaction.followup.send("Unexpected error while reviewing. Check the bot logs for details.")
            return

        score = score_of(result)
        before = self._rating_store.get(interaction.user.id).rating
        delta = compute_delta(before, score)
        record = await self._rating_store.apply_delta(interaction.user.id, delta)

        await interaction.followup.send(
            embed=_review_embed(interaction.user, result, score, before, delta, record)
        )


def _review_embed(
    user: discord.abc.User,
    result: ReviewResult,
    score: int,
    before: int,
    delta: int,
    record: UserRating,
) -> discord.Embed:
    up = delta >= 0
    arrow = "📈" if up else "📉"
    sign = "+" if up else ""
    tier_name, tier_color = tier_for(record.rating)

    embed = discord.Embed(
        title=f"Verdict: {result.verdict}",
        description=result.summary or None,
        colour=discord.Colour(0x2ECC71 if up else 0xE74C3C),
    )
    embed.set_author(name=f"{user.display_name} — rated review")
    embed.add_field(
        name="Scores",
        value=(
            f"Correctness `{result.correctness}/10`\n"
            f"Efficiency `{result.efficiency}/10`\n"
            f"Readability `{result.readability}/10`\n"
            f"Overall `{score}/100`"
        ),
        inline=True,
    )
    embed.add_field(
        name="Rating",
        value=f"`{before}` → `{record.rating}`  {arrow} **{sign}{delta}**\n{tier_name}",
        inline=True,
    )
    embed.set_footer(text=f"Submission #{record.submissions}")
    return embed


def _rating_embed(user: discord.abc.User, record: UserRating) -> discord.Embed:
    tier_name, tier_color = tier_for(record.rating)
    embed = discord.Embed(
        title=f"{user.display_name}'s rating",
        colour=discord.Colour(tier_color),
    )
    embed.add_field(name="Rating", value=f"**{record.rating}**  ·  {tier_name}", inline=False)

    if not record.is_rated:
        embed.description = "Unrated so far — run `/submit` to review your first solution and get on the board!"
        return embed

    embed.add_field(name="Submissions", value=str(record.submissions), inline=True)
    embed.add_field(name="Best", value=f"+{record.best_delta}", inline=True)
    embed.add_field(name="Worst", value=str(record.worst_delta), inline=True)
    return embed


async def _ensure_allowed_guild(interaction: discord.Interaction, settings: Settings) -> bool:
    if interaction.guild_id != settings.discord_guild_id:
        await interaction.response.send_message("This bot is not enabled in this server.", ephemeral=True)
        return False
    return True
