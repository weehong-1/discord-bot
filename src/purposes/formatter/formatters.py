from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass

from discord import app_commands

from ...services.ai_client import AIClient

LOGGER = logging.getLogger(__name__)

SUBPROCESS_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class FormatterSpec:
    """How to run a real, installed formatter for a language.

    The code is always piped over stdin and the formatted result is read from
    stdout, so `argv` must name a tool that supports stdin/stdout filtering.
    """

    argv: tuple[str, ...]

    @property
    def tool(self) -> str:
        return self.argv[0]


# Language value -> real formatter that reads stdin and writes stdout.
# The value strings double as the Discord syntax-highlight tag.
FORMATTERS: dict[str, FormatterSpec] = {
    "python": FormatterSpec(("black", "-q", "-")),
    "javascript": FormatterSpec(("prettier", "--stdin-filepath", "file.js")),
    "typescript": FormatterSpec(("prettier", "--stdin-filepath", "file.ts")),
    "json": FormatterSpec(("prettier", "--stdin-filepath", "file.json")),
    "css": FormatterSpec(("prettier", "--stdin-filepath", "file.css")),
    "html": FormatterSpec(("prettier", "--stdin-filepath", "file.html")),
    "go": FormatterSpec(("gofmt",)),
    "rust": FormatterSpec(("rustfmt", "--emit", "stdout")),
    "cpp": FormatterSpec(("clang-format",)),
    "java": FormatterSpec(("clang-format", "--assume-filename=file.java")),
}

# Dropdown shown on the /code command. `value` is the Discord syntax tag.
FORMATTER_CHOICES = [
    app_commands.Choice(name="Python", value="python"),
    app_commands.Choice(name="JavaScript", value="javascript"),
    app_commands.Choice(name="TypeScript", value="typescript"),
    app_commands.Choice(name="JSON", value="json"),
    app_commands.Choice(name="CSS", value="css"),
    app_commands.Choice(name="HTML", value="html"),
    app_commands.Choice(name="Go", value="go"),
    app_commands.Choice(name="Rust", value="rust"),
    app_commands.Choice(name="C++", value="cpp"),
    app_commands.Choice(name="Java", value="java"),
]

_FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)

# Syntax tag (incl. common LLM-detected aliases) -> file extension for attachments.
_EXTENSIONS = {
    "python": "py",
    "py": "py",
    "javascript": "js",
    "js": "js",
    "typescript": "ts",
    "ts": "ts",
    "json": "json",
    "css": "css",
    "html": "html",
    "go": "go",
    "rust": "rs",
    "rs": "rs",
    "cpp": "cpp",
    "c++": "cpp",
    "c": "c",
    "java": "java",
    "csharp": "cs",
    "c#": "cs",
    "kotlin": "kt",
    "ruby": "rb",
    "php": "php",
    "swift": "swift",
    "bash": "sh",
    "sh": "sh",
    "shell": "sh",
    "sql": "sql",
    "yaml": "yaml",
    "yml": "yaml",
}


def extension_for(tag: str) -> str:
    """Map a syntax tag to a file extension for attachments; default to txt."""
    return _EXTENSIONS.get(tag.lower(), "txt")


async def format_code(code: str, language: str | None, ai_client: AIClient) -> tuple[str, str]:
    """Format `code` and return (formatted_code, syntax_tag).

    Hybrid strategy: when a language is chosen and a real formatter for it is
    installed, shell out to that tool. Otherwise (no language, missing tool, or a
    formatter failure) fall back to the LLM, which also detects the language when
    none was given.
    """
    code = code.strip()
    if language:
        formatted = await _run_real_formatter(code, language)
        if formatted is not None:
            return formatted, language

    return await _llm_format(code, language, ai_client)


async def _run_real_formatter(code: str, language: str) -> str | None:
    """Run the installed formatter for `language`; return None to fall back to the LLM."""
    spec = FORMATTERS.get(language)
    if spec is None or shutil.which(spec.tool) is None:
        return None

    try:
        process = await asyncio.create_subprocess_exec(
            *spec.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        LOGGER.exception("Could not launch formatter %s", spec.tool)
        return None

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=code.encode("utf-8")),
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        process.kill()
        LOGGER.warning("Formatter %s timed out", spec.tool)
        return None

    if process.returncode != 0:
        LOGGER.warning(
            "Formatter %s exited with %s: %s",
            spec.tool,
            process.returncode,
            stderr.decode("utf-8", "replace").strip()[:300],
        )
        return None

    formatted = stdout.decode("utf-8", "replace").strip()
    return formatted or None


async def _llm_format(code: str, language: str | None, ai_client: AIClient) -> tuple[str, str]:
    answer = await ai_client.complete(_build_format_messages(code, language))
    tag, body = extract_fenced_code(answer.content)
    syntax_tag = language or tag or "text"
    return body, syntax_tag


def _build_format_messages(code: str, language: str | None) -> list[dict[str, str]]:
    if language:
        target = f"The code is written in {language}."
    else:
        target = "Detect the programming language yourself."

    system = (
        "You are a code formatter, like Prettier or Black. Reformat the code the user gives "
        "you to clean, idiomatic, industry-standard style. "
        "Only change whitespace, indentation, and layout. Do NOT change logic, rename anything, "
        "add or remove comments, or explain anything. "
        f"{target} "
        "Respond with exactly one Markdown code block whose info string is the correct language "
        "tag (for example ```python), containing only the formatted code and nothing else."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Format this code:\n{code}"},
    ]


def extract_fenced_code(text: str) -> tuple[str, str]:
    """Pull (language_tag, body) out of the first fenced block in `text`.

    Falls back to ("", stripped text) when no fence is present.
    """
    match = _FENCE_RE.search(text)
    if match is None:
        return "", text.strip()
    return match.group(1).strip(), match.group(2).strip()
