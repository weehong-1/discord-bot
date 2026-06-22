from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


LOGGER = logging.getLogger(__name__)

DEFAULT_RATINGS_PATH = Path("data/ratings.json")

# Everyone starts here, Codeforces-style.
STARTING_RATING = 1500


@dataclass
class UserRating:
    user_id: int
    rating: int = STARTING_RATING
    submissions: int = 0
    best_delta: int = 0
    worst_delta: int = 0
    updated_at: datetime | None = None

    @property
    def is_rated(self) -> bool:
        """Whether this user has ever submitted (vs. a fresh default)."""
        return self.submissions > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "rating": self.rating,
            "submissions": self.submissions,
            "best_delta": self.best_delta,
            "worst_delta": self.worst_delta,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "UserRating":
        updated_raw = data.get("updated_at")
        return cls(
            user_id=int(data["user_id"]),  # type: ignore[arg-type]
            rating=int(data["rating"]),  # type: ignore[arg-type]
            submissions=int(data["submissions"]),  # type: ignore[arg-type]
            best_delta=int(data["best_delta"]),  # type: ignore[arg-type]
            worst_delta=int(data["worst_delta"]),  # type: ignore[arg-type]
            updated_at=_parse_dt(str(updated_raw)) if updated_raw else None,
        )


class RatingStore:
    """JSON-backed per-user rating persistence with an in-memory mirror.

    Structurally mirrors ``remind.store.ReminderStore``: a single asyncio lock
    guards mutations, every change is flushed atomically (temp file + os.replace),
    and ``load`` never raises on malformed input.
    """

    def __init__(self, path: Path = DEFAULT_RATINGS_PATH) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._ratings: dict[int, UserRating] = {}

    def load(self) -> list[UserRating]:
        """Read ratings from disk into memory. Never raises on bad input."""
        self._ratings = {}
        if not self._path.exists():
            return []

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.warning("Could not read ratings file %s; starting empty.", self._path, exc_info=True)
            return []

        if not isinstance(raw, list):
            LOGGER.warning("Ratings file %s is not a list; starting empty.", self._path)
            return []

        for entry in raw:
            try:
                record = UserRating.from_dict(entry)
            except (KeyError, TypeError, ValueError):
                LOGGER.warning("Skipping malformed rating entry: %r", entry, exc_info=True)
                continue
            self._ratings[record.user_id] = record

        return list(self._ratings.values())

    def get(self, user_id: int) -> UserRating:
        """Return the user's record, or a fresh unsaved default at STARTING_RATING."""
        existing = self._ratings.get(user_id)
        if existing is not None:
            return existing
        return UserRating(user_id=user_id)

    async def apply_delta(self, user_id: int, delta: int, now: datetime | None = None) -> UserRating:
        """Apply a rating change for `user_id`, persist, and return the new record."""
        async with self._lock:
            record = self._ratings.get(user_id)
            if record is None:
                record = UserRating(user_id=user_id)
                self._ratings[user_id] = record

            record.rating += delta
            record.submissions += 1
            if record.submissions == 1:
                record.best_delta = delta
                record.worst_delta = delta
            else:
                record.best_delta = max(record.best_delta, delta)
                record.worst_delta = min(record.worst_delta, delta)
            record.updated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

            self._flush()
            return record

    def _flush(self) -> None:
        """Atomically rewrite the JSON file. Caller must hold the lock."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [r.to_dict() for r in self._ratings.values()]
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
