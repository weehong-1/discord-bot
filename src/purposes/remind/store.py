from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


LOGGER = logging.getLogger(__name__)

DEFAULT_REMINDERS_PATH = Path("data/reminders.json")


@dataclass
class Reminder:
    id: str
    group_id: str
    user_id: int
    channel_id: int
    guild_id: int
    frontend_id: str
    title: str
    slug: str
    fire_at: datetime
    created_at: datetime
    # Channel to deliver to if `channel_id` is gone at fire time (e.g. a deleted
    # thread): the thread's parent channel, captured when the reminder was created.
    fallback_channel_id: int | None = None

    @property
    def url(self) -> str:
        return f"https://leetcode.com/problems/{self.slug}/"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "frontend_id": self.frontend_id,
            "title": self.title,
            "slug": self.slug,
            "fire_at": self.fire_at.isoformat(),
            "created_at": self.created_at.isoformat(),
            "fallback_channel_id": self.fallback_channel_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Reminder":
        return cls(
            id=str(data["id"]),
            group_id=str(data["group_id"]),
            user_id=int(data["user_id"]),  # type: ignore[arg-type]
            channel_id=int(data["channel_id"]),  # type: ignore[arg-type]
            guild_id=int(data["guild_id"]),  # type: ignore[arg-type]
            frontend_id=str(data["frontend_id"]),
            title=str(data["title"]),
            slug=str(data["slug"]),
            fire_at=_parse_dt(str(data["fire_at"])),
            created_at=_parse_dt(str(data["created_at"])),
            fallback_channel_id=_opt_int(data.get("fallback_channel_id")),
        )


class ReminderStore:
    """JSON-backed reminder persistence with an in-memory mirror."""

    def __init__(self, path: Path = DEFAULT_REMINDERS_PATH) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._reminders: dict[str, Reminder] = {}

    def load(self) -> list[Reminder]:
        """Read reminders from disk into memory. Never raises on bad input."""
        self._reminders = {}
        if not self._path.exists():
            return []

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.warning("Could not read reminders file %s; starting empty.", self._path, exc_info=True)
            return []

        if not isinstance(raw, list):
            LOGGER.warning("Reminders file %s is not a list; starting empty.", self._path)
            return []

        for entry in raw:
            try:
                reminder = Reminder.from_dict(entry)
            except (KeyError, TypeError, ValueError):
                LOGGER.warning("Skipping malformed reminder entry: %r", entry, exc_info=True)
                continue
            self._reminders[reminder.id] = reminder

        return list(self._reminders.values())

    async def add(self, reminder: Reminder) -> None:
        async with self._lock:
            self._reminders[reminder.id] = reminder
            self._flush()

    async def remove(self, reminder_id: str) -> None:
        async with self._lock:
            if self._reminders.pop(reminder_id, None) is not None:
                self._flush()

    async def remove_user(self, user_id: int) -> int:
        async with self._lock:
            to_remove = [rid for rid, r in self._reminders.items() if r.user_id == user_id]
            for rid in to_remove:
                del self._reminders[rid]
            if to_remove:
                self._flush()
            return len(to_remove)

    async def list_user(self, user_id: int) -> list[Reminder]:
        async with self._lock:
            reminders = [r for r in self._reminders.values() if r.user_id == user_id]
        return sorted(reminders, key=lambda r: r.fire_at)

    def _flush(self) -> None:
        """Atomically rewrite the JSON file. Caller must hold the lock."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [r.to_dict() for r in self._reminders.values()]
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)


def _opt_int(value: object) -> int | None:
    return int(value) if value is not None else None  # type: ignore[arg-type]


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
