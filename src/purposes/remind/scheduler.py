from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import discord

from .store import Reminder, ReminderStore
from .views import DismissView


LOGGER = logging.getLogger(__name__)


class ReminderScheduler:
    """Schedules reminder deliveries and reloads them across restarts."""

    def __init__(self, bot: discord.Client, store: ReminderStore) -> None:
        self._bot = bot
        self._store = store
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._started = False

    @property
    def store(self) -> ReminderStore:
        return self._store

    async def start(self) -> None:
        """Load persisted reminders and schedule them. Safe to call repeatedly."""
        if self._started:
            return
        self._started = True

        reminders = self._store.load()
        for reminder in reminders:
            self.schedule(reminder)
        LOGGER.info("Reminder scheduler started with %d pending reminder(s).", len(reminders))

    async def add(self, reminder: Reminder) -> None:
        """Persist a new reminder and schedule its delivery."""
        await self._store.add(reminder)
        self.schedule(reminder)

    def schedule(self, reminder: Reminder) -> None:
        if reminder.id in self._tasks:
            return
        self._tasks[reminder.id] = asyncio.create_task(self._run(reminder))

    async def cancel(self, reminder_id: str) -> None:
        task = self._tasks.pop(reminder_id, None)
        if task is not None and not task.done():
            task.cancel()
        await self._store.remove(reminder_id)

    async def cancel_user(self, user_id: int) -> int:
        reminders = await self._store.list_user(user_id)
        for reminder in reminders:
            task = self._tasks.pop(reminder.id, None)
            if task is not None and not task.done():
                task.cancel()
        return await self._store.remove_user(user_id)

    async def _run(self, reminder: Reminder) -> None:
        delay = (reminder.fire_at - datetime.now(timezone.utc)).total_seconds()
        try:
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        try:
            await self._fire(reminder)
        except Exception:
            LOGGER.exception("Failed to deliver reminder %s", reminder.id)
        finally:
            self._tasks.pop(reminder.id, None)
            await self._store.remove(reminder.id)

    async def _fire(self, reminder: Reminder) -> None:
        content = (
            f"<@{reminder.user_id}> ⏰ Time to revisit LeetCode "
            f"`{reminder.frontend_id}. {reminder.title}`\n{reminder.url}"
        )

        # Try the original channel, then the parent channel (in case the original
        # was a thread that got deleted), then fall back to a direct message.
        candidate_ids = [reminder.channel_id]
        if reminder.fallback_channel_id and reminder.fallback_channel_id != reminder.channel_id:
            candidate_ids.append(reminder.fallback_channel_id)

        for channel_id in candidate_ids:
            channel = await self._resolve_channel(channel_id)
            if channel is not None:
                await channel.send(
                    content,
                    allowed_mentions=discord.AllowedMentions(users=True),
                    view=DismissView(),
                )
                return

        user = self._bot.get_user(reminder.user_id)
        if user is None:
            try:
                user = await self._bot.fetch_user(reminder.user_id)
            except discord.HTTPException:
                user = None
        if user is not None:
            try:
                await user.send(content, view=DismissView())
                return
            except discord.HTTPException:
                LOGGER.warning("Could not DM user %s for reminder %s.", reminder.user_id, reminder.id)

        LOGGER.warning("No deliverable channel for reminder %s; dropping.", reminder.id)

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        channel = self._bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(channel_id)
            except discord.HTTPException:
                return None
        return channel if isinstance(channel, discord.abc.Messageable) else None
