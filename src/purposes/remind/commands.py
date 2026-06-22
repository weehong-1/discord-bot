from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands

from ...config import Settings
from ..leetcode.leetcode_client import LeetCodeClient, LeetCodeError
from .scheduler import ReminderScheduler
from .store import Reminder
from .views import DismissView


LOGGER = logging.getLogger(__name__)

MAX_MINUTES = 7 * 24 * 60
MAX_HOURS = 7 * 24
MAX_DAYS = 365

# Spaced-repetition presets: choice value is a comma-separated list of day offsets.
SPACED_PRESETS: dict[str, list[int]] = {
    "1,3,7": [1, 3, 7],
    "1,3,7,14": [1, 3, 7, 14],
    "1,3,7,14,30": [1, 3, 7, 14, 30],
}
SPACED_CHOICES = [
    app_commands.Choice(name="1, 3, 7 days", value="1,3,7"),
    app_commands.Choice(name="1, 3, 7, 14 days", value="1,3,7,14"),
    app_commands.Choice(name="1, 3, 7, 14, 30 days", value="1,3,7,14,30"),
]

_DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d")


def register_commands(
    tree: app_commands.CommandTree[discord.Client],
    settings: Settings,
    leetcode_client: LeetCodeClient,
    scheduler: ReminderScheduler,
) -> None:
    @tree.command(name="remind", description="Schedule a reminder to revisit a LeetCode problem.")
    @app_commands.describe(
        problem="LeetCode number, slug, or URL, e.g. 2314, two-sum, or a problem URL",
        minutes="Remind in this many minutes (combines with hours/days)",
        hours="Remind in this many hours (combines with minutes/days)",
        days="Remind in this many days (combines with minutes/hours)",
        date="Specific date/time in server time: YYYY-MM-DD or YYYY-MM-DD HH:MM",
        spaced="Spaced repetition: create several reminders at once",
    )
    @app_commands.choices(spaced=SPACED_CHOICES)
    async def remind(
        interaction: discord.Interaction,
        problem: str,
        minutes: app_commands.Range[int, 1, MAX_MINUTES] | None = None,
        hours: app_commands.Range[int, 1, MAX_HOURS] | None = None,
        days: app_commands.Range[int, 1, MAX_DAYS] | None = None,
        date: str | None = None,
        spaced: app_commands.Choice[str] | None = None,
    ) -> None:
        if not await _ensure_allowed_guild(interaction, settings):
            return

        if interaction.guild_id is None or interaction.channel_id is None:
            await interaction.response.send_message("Run `/remind` inside a server channel.", ephemeral=True)
            return

        now = datetime.now(timezone.utc)
        try:
            fire_times = _resolve_fire_times(now, minutes, hours, days, date, spaced)
        except _TimingError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            info = await leetcode_client.resolve_problem(problem)
        except LeetCodeError as exc:
            await interaction.followup.send(f"I could not find that LeetCode problem: {exc}")
            return
        except Exception:
            LOGGER.exception("Unexpected error while resolving /remind problem")
            await interaction.followup.send("Unexpected error while looking up the problem. Check the bot logs for details.")
            return

        # If invoked inside a thread, remember the parent channel so the reminder
        # still fires if the thread is later deleted.
        fallback_channel_id = (
            interaction.channel.parent_id if isinstance(interaction.channel, discord.Thread) else None
        )

        group_id = uuid.uuid4().hex
        for fire_at in fire_times:
            reminder = Reminder(
                id=uuid.uuid4().hex,
                group_id=group_id,
                user_id=interaction.user.id,
                channel_id=interaction.channel_id,
                guild_id=interaction.guild_id,
                frontend_id=info.frontend_id,
                title=info.title,
                slug=info.slug,
                fire_at=fire_at,
                created_at=now,
                fallback_channel_id=fallback_channel_id,
            )
            await scheduler.add(reminder)

        url = f"https://leetcode.com/problems/{info.slug}/"
        header = f"⏰ Reminder set for `{info.frontend_id}. {info.title}` ({url})"
        if len(fire_times) == 1:
            body = f"\nI will ping you here {_discord_ts(fire_times[0])}."
        else:
            lines = "\n".join(f"- {_discord_ts(ft)}" for ft in fire_times)
            body = f"\nI will ping you here at {len(fire_times)} times:\n{lines}"
        await interaction.followup.send(header + body, view=DismissView())

    @tree.command(name="reminders", description="List your pending LeetCode reminders.")
    async def reminders(interaction: discord.Interaction) -> None:
        if not await _ensure_allowed_guild(interaction, settings):
            return

        pending = await scheduler.store.list_user(interaction.user.id)
        if not pending:
            await interaction.response.send_message("You have no pending reminders.", ephemeral=True)
            return

        lines = ["Your pending reminders:"]
        lines.extend(
            f"- `{r.frontend_id}. {r.title}` {_discord_ts(r.fire_at)} (`{r.id[:8]}`)" for r in pending
        )
        view = ClearAllView(owner_id=interaction.user.id, scheduler=scheduler)
        await interaction.response.send_message("\n".join(lines), view=view, ephemeral=True)


class ClearAllView(discord.ui.View):
    def __init__(self, owner_id: int, scheduler: ReminderScheduler) -> None:
        super().__init__(timeout=5 * 60)
        self.owner_id = owner_id
        self.scheduler = scheduler

    @discord.ui.button(label="Clear all", style=discord.ButtonStyle.danger)
    async def clear_all(self, interaction: discord.Interaction, button: discord.ui.Button["ClearAllView"]) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the person who ran the command can clear these.", ephemeral=True)
            return

        removed = await self.scheduler.cancel_user(self.owner_id)
        button.disabled = True
        await interaction.response.edit_message(
            content=f"Cleared {removed} reminder{'s' if removed != 1 else ''}.",
            view=self,
        )


class _TimingError(ValueError):
    pass


def _resolve_fire_times(
    now: datetime,
    minutes: int | None,
    hours: int | None,
    days: int | None,
    date: str | None,
    spaced: app_commands.Choice[str] | None,
) -> list[datetime]:
    has_delay = any(v is not None for v in (minutes, hours, days))
    modes_used = sum((has_delay, date is not None, spaced is not None))

    if modes_used == 0:
        raise _TimingError(
            "Tell me when: use `minutes`/`hours`/`days`, a `date`, or a `spaced` preset."
        )
    if modes_used > 1:
        raise _TimingError(
            "Pick one timing method only: a delay (`minutes`/`hours`/`days`), a `date`, or `spaced`."
        )

    if spaced is not None:
        offsets = SPACED_PRESETS[spaced.value]
        return [now + timedelta(days=d) for d in offsets]

    if date is not None:
        fire_at = _parse_date(date)
        if fire_at <= now:
            raise _TimingError("That date is in the past. Pick a future date/time.")
        return [fire_at]

    delta = timedelta(
        minutes=minutes or 0,
        hours=hours or 0,
        days=days or 0,
    )
    return [now + delta]


def _parse_date(value: str) -> datetime:
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        # Interpret the naive value as server-local time, then store as UTC.
        return naive.astimezone().astimezone(timezone.utc)
    raise _TimingError("Use a date like `2026-06-25` or `2026-06-25 14:30`.")


def _discord_ts(dt: datetime) -> str:
    unix = int(dt.timestamp())
    return f"<t:{unix}:f> (<t:{unix}:R>)"


async def _ensure_allowed_guild(interaction: discord.Interaction, settings: Settings) -> bool:
    if interaction.guild_id != settings.discord_guild_id:
        await interaction.response.send_message("This bot is not enabled in this server.", ephemeral=True)
        return False
    return True
