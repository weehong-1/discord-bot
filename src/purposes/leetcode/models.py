from __future__ import annotations

from discord import app_commands

from ...services.models import MODEL_ALLOWLIST

MODEL_CHOICES = [
    app_commands.Choice(name="DeepSeek R1", value="deepseek-r1"),
    app_commands.Choice(name="DeepSeek Chat", value="deepseek-chat"),
    app_commands.Choice(name="GPT-4o Mini", value="gpt-4o-mini"),
    app_commands.Choice(name="GPT-4.1 Mini", value="gpt-4.1-mini"),
    app_commands.Choice(name="Claude Sonnet", value="claude-sonnet"),
    app_commands.Choice(name="Gemini Flash", value="gemini-flash"),
    app_commands.Choice(name="Llama 405B", value="llama-405b"),
]
