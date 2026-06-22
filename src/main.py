from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import discord
from discord import app_commands

from .config import load_settings
from .purposes.formatter.commands import register_commands as register_formatter_commands
from .purposes.leetcode.commands import answer_mention as answer_leetcode_mention
from .purposes.leetcode.commands import is_channel_in_scope
from .purposes.leetcode.commands import register_commands as register_leetcode_commands
from .purposes.leetcode.leetcode_client import LeetCodeClient
from .purposes.rating.commands import register_commands as register_rating_commands
from .purposes.rating.store import RatingStore
from .purposes.remind.commands import register_commands as register_remind_commands
from .purposes.remind.scheduler import ReminderScheduler
from .purposes.remind.store import ReminderStore
from .purposes.remind.views import DismissView
from .purposes.timer.commands import register_commands as register_timer_commands
from .services.ai_client import AIClient
from .services.context import collect_recent_context


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger(__name__)

settings = load_settings()
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
ai_client = AIClient(settings)
leetcode_client = LeetCodeClient()
reminder_store = ReminderStore()
reminder_scheduler = ReminderScheduler(bot, reminder_store)
rating_store = RatingStore()
rating_store.load()
register_leetcode_commands(tree, settings, ai_client, leetcode_client)
register_timer_commands(tree, settings)
register_formatter_commands(tree, settings, ai_client)
register_remind_commands(tree, settings, leetcode_client, reminder_scheduler)
register_rating_commands(tree, settings, ai_client, rating_store)
_startup_complete = False
_discord_api_blocked_until = 0.0


@bot.event
async def on_ready() -> None:
    global _startup_complete

    if _startup_complete:
        LOGGER.info("Reconnected as %s.", bot.user)
        return

    guild = discord.Object(id=settings.discord_guild_id)
    tree.copy_global_to(guild=guild)
    await tree.sync(guild=guild)
    bot.add_view(DismissView())
    await reminder_scheduler.start()
    _startup_complete = True
    LOGGER.info("Logged in as %s. Slash commands synced to guild %s.", bot.user, settings.discord_guild_id)


@bot.event
async def on_message(message: discord.Message) -> None:
    global _discord_api_blocked_until

    if message.author.bot:
        return

    if time.monotonic() < _discord_api_blocked_until:
        return

    if message.guild is None or message.guild.id != settings.discord_guild_id:
        return

    if not is_channel_in_scope(message.channel, settings):
        return

    if bot.user is None or not any(user.id == bot.user.id for user in message.mentions):
        return

    text = _strip_bot_mentions(message.content, bot.user.id).strip()
    if not text:
        return

    try:
        context = await collect_recent_context(message.channel, before=message, limit=12)
        await answer_leetcode_mention(message, ai_client, text, context=context)
    except discord.HTTPException as exc:
        retry_after = _discord_retry_after(exc)
        _discord_api_blocked_until = time.monotonic() + retry_after
        LOGGER.warning(
            "Discord API request failed while handling a mention; pausing message handling for %.1f seconds: %s",
            retry_after,
            exc,
        )


def main() -> None:
    _start_health_server()
    try:
        asyncio.run(_run_bot_with_login_backoff())
    finally:
        asyncio.run(_close_clients())


async def _run_bot_with_login_backoff() -> None:
    while True:
        try:
            await bot.start(settings.discord_bot_token)
        except discord.LoginFailure:
            raise
        except discord.HTTPException as exc:
            if getattr(exc, "status", None) != 429:
                raise

            retry_after = _discord_retry_after(exc)
            LOGGER.warning(
                "Discord API returned a global rate limit during login; retrying in %.1f seconds: %s",
                retry_after,
                exc,
            )
            await asyncio.sleep(retry_after)
        else:
            return


async def _close_clients() -> None:
    await ai_client.close()
    await leetcode_client.close()


def _strip_bot_mentions(content: str, bot_user_id: int) -> str:
    mention_forms = (f"<@{bot_user_id}>", f"<@!{bot_user_id}>")
    for mention in mention_forms:
        content = content.replace(mention, "")
    return content


def _discord_retry_after(exc: discord.HTTPException) -> float:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        value = headers.get("Retry-After")
        if value is not None:
            try:
                return max(float(value), 60.0)
            except ValueError:
                pass
    return 10 * 60.0


def _start_health_server() -> None:
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    LOGGER.info("Health server listening on port %s.", port)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            return

        body = b"ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.debug("health check: " + format, *args)


if __name__ == "__main__":
    main()
