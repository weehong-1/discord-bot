from __future__ import annotations

import re

import discord


async def collect_recent_context(
    channel: discord.abc.Messageable,
    *,
    before: discord.Message | None = None,
    limit: int = 12,
    max_chars: int = 3500,
) -> str:
    if not hasattr(channel, "history"):
        return ""

    messages: list[str] = []
    history = channel.history(limit=limit, before=before, oldest_first=True)  # type: ignore[attr-defined]
    async for message in history:
        content = _message_text(message)
        if not content:
            continue
        author = getattr(message.author, "display_name", message.author.name)
        messages.append(f"{author}: {content}")

    context = "\n".join(messages).strip()
    if len(context) <= max_chars:
        return context
    return context[-max_chars:].lstrip()


def _message_text(message: discord.Message) -> str:
    parts: list[str] = []
    content = _clean_content(message.clean_content)
    if content:
        parts.append(content)

    for embed in message.embeds:
        embed_parts = []
        if embed.title:
            embed_parts.append(embed.title)
        if embed.description:
            embed_parts.append(embed.description)
        for field in embed.fields[:4]:
            embed_parts.append(f"{field.name}: {field.value}")
        if embed_parts:
            parts.append(" | ".join(_clean_content(part) for part in embed_parts if part))

    return " ".join(parts).strip()


def _clean_content(content: str) -> str:
    content = re.sub(r"\s+", " ", content).strip()
    return content[:800]
