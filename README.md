# Discord Multi-Purpose Bot

A private Discord bot currently focused on LeetCode assistance. It uses OpenRouter by default with a small allowlist of popular models and falls back to the DeepSeek API when configured.

The project is structured as a multi-purpose Discord bot. The LeetCode Assistant is isolated under `src/purposes/leetcode/` so new bot purposes can be added without mixing feature code.

## Features

- Mentioning the bot starts a LeetCode Assistant response in the configured server
- Recent channel/thread context for mentioned messages
- `/models` command to show allowed model keys
- OpenRouter primary provider
- DeepSeek direct API fallback
- Fixed Discord guild restriction; mentioned messages and utility commands work in any server channel or thread the bot can access
- Assistant-style LeetCode prompt
- Long Discord replies split into safe chunks

## Project Structure

```text
src/
  main.py                         # Discord startup, command sync, cleanup
  config.py                       # Environment loading
  purposes/
    leetcode/
      commands.py                 # /models, LeetCode message handling
      leetcode_client.py          # LeetCode problem resolver
      models.py                   # Model allowlist
      tutor.py                    # Assistant prompt (chunking re-exported from services)
  services/
    ai_client.py                  # OpenRouter + DeepSeek fallback
    context.py                    # Recent channel/thread context collection
    discord_text.py               # Shared Discord message chunking
    models.py                     # Shared model allowlist
```

When adding another purpose later, create another directory under `src/purposes/` and register its commands from `src/main.py`.

## Setup

Create a Discord application and bot at <https://discord.com/developers/applications>.

Enable these bot permissions when inviting it:

- `applications.commands`
- `bot`
- Send Messages
- Send Messages in Threads
- Create Public Threads
- Use Slash Commands
- Read Message History

Enable **Message Content Intent** in the Discord Developer Portal under **Bot**. The LeetCode Assistant session uses mentioned channel/thread messages, so this intent is required.

The bot uses recent messages from the current channel or thread as context, so mentions like "@Bot explain this question" can refer to a nearby LeetCode reminder or previous conversation. It needs `Read Message History` and `Message Content Intent` for this to work well.

Install dependencies:

```bash
source venv/bin/activate
pip install -r requirements.txt
```

Create local config:

```bash
cp .env.example .env
```

Fill in `.env`:

```env
DISCORD_BOT_TOKEN=your_discord_bot_token
DISCORD_GUILD_ID=your_server_id
DISCORD_CHANNEL_ID=

OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_MODEL_KEY=gemini-flash
OPENROUTER_APP_NAME=LeetCode Assistant Bot
OPENROUTER_SITE_URL=

DEEPSEEK_API_KEY=your_deepseek_key_optional_but_recommended
DEEPSEEK_MODEL=deepseek-chat
```

Run the bot:

```bash
python -m src.main
```

## Allowed OpenRouter Models

Use these keys as `OPENROUTER_MODEL_KEY`:

| Key | OpenRouter model |
| --- | --- |
| `deepseek-r1` | `deepseek/deepseek-r1` |
| `deepseek-chat` | `deepseek/deepseek-chat-v3-0324` |
| `gpt-4o-mini` | `openai/gpt-4o-mini` |
| `gpt-4.1-mini` | `openai/gpt-4.1-mini` |
| `claude-sonnet` | `anthropic/claude-3.5-sonnet` |
| `gemini-flash` | `google/gemini-3.5-flash` |
| `llama-405b` | `meta-llama/llama-3.1-405b-instruct` |

Set the default model with:

```env
OPENROUTER_MODEL_KEY=gemini-flash
```

## Discord IDs

Enable Developer Mode in Discord, then right-click your server to copy its ID. `DISCORD_CHANNEL_ID` is currently optional and not used for restriction; the bot is allowed across the configured server, including threads it can access.

## Notes

- `.env` is ignored by git and should contain your real tokens.
- DeepSeek fallback only works if `DEEPSEEK_API_KEY` is set.
- Slash commands are synced to the configured guild on startup.
