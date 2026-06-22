from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .services.models import MODEL_ALLOWLIST


@dataclass(frozen=True)
class Settings:
    discord_bot_token: str
    discord_guild_id: int
    discord_channel_id: int | None
    openrouter_api_key: str
    openrouter_model_key: str
    openrouter_app_name: str
    openrouter_site_url: str | None
    deepseek_api_key: str | None
    deepseek_model: str

    @property
    def openrouter_model_id(self) -> str:
        return MODEL_ALLOWLIST[self.openrouter_model_key]


def load_settings() -> Settings:
    load_dotenv()

    discord_bot_token = _required("DISCORD_BOT_TOKEN")
    discord_guild_id = _required_int("DISCORD_GUILD_ID")
    discord_channel_id = _optional_int("DISCORD_CHANNEL_ID")
    openrouter_api_key = _required("OPENROUTER_API_KEY")
    openrouter_model_key = os.getenv("OPENROUTER_MODEL_KEY", "gemini-flash").strip()

    if openrouter_model_key not in MODEL_ALLOWLIST:
        allowed = ", ".join(sorted(MODEL_ALLOWLIST))
        raise ValueError(
            f"OPENROUTER_MODEL_KEY must be one of: {allowed}. "
            f"Got: {openrouter_model_key!r}"
        )

    openrouter_site_url = os.getenv("OPENROUTER_SITE_URL", "").strip() or None
    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "").strip() or None

    return Settings(
        discord_bot_token=discord_bot_token,
        discord_guild_id=discord_guild_id,
        discord_channel_id=discord_channel_id,
        openrouter_api_key=openrouter_api_key,
        openrouter_model_key=openrouter_model_key,
        openrouter_app_name=os.getenv("OPENROUTER_APP_NAME", "LeetCode Assistant Bot").strip(),
        openrouter_site_url=openrouter_site_url,
        deepseek_api_key=deepseek_api_key,
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip(),
    )


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _required_int(name: str) -> int:
    value = _required(name)
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer") from exc


def _optional_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer") from exc
