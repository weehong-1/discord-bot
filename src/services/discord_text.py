from __future__ import annotations

FENCE = "```"
_EMPTY_RESPONSE = "I did not receive a response from the model."


def chunk_for_discord(text: str, limit: int = 1900) -> list[str]:
    """Split `text` into Discord-sized messages without breaking code fences.

    Discord caps messages at 2000 characters, so long model answers must be
    split. A naive split is dangerous for fenced code: if a chunk boundary
    lands inside a ``` block, the first message is left with an unclosed
    fence and the next message renders as plain text. In plain text Discord
    treats ``||`` (a common logical-OR in code) as spoiler markup and hides
    everything between the pair, so the source silently disappears.

    To avoid that we track fence state line by line. When a split happens
    while a code block is open we close the fence on the outgoing chunk and
    reopen it -- with the same info string, e.g. ``` java -- on the next one.
    """
    text = text.strip()
    if not text:
        return [_EMPTY_RESPONSE]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    fence: str | None = None  # the open fence's info line (e.g. "```java"), or None

    def flush(reopen: bool) -> None:
        nonlocal current, current_len
        if not current:
            return
        body = current + [FENCE] if fence is not None else current
        chunks.append("\n".join(body).strip())
        if reopen and fence is not None:
            current = [fence]
            current_len = len(fence) + 1
        else:
            current = []
            current_len = 0

    for raw_line in text.split("\n"):
        for line in _split_long_line(raw_line, limit):
            added = len(line) + 1  # +1 for the newline joining it to the chunk
            if current and current_len + added > limit:
                flush(reopen=True)
            current.append(line)
            current_len += added
            if line.lstrip().startswith(FENCE):
                # A fence line toggles in/out of a code block.
                fence = None if fence is not None else line.strip()

    flush(reopen=False)
    return [chunk for chunk in chunks if chunk] or [_EMPTY_RESPONSE]


def _split_long_line(line: str, limit: int) -> list[str]:
    """Hard-wrap a single line that is itself longer than `limit`.

    Code lines are rarely this long, but a runaway line would otherwise
    overflow a Discord message. Reserve room so a reopened/closed fence still
    fits alongside the wrapped piece.
    """
    budget = max(limit - len(FENCE) - 1, 1)
    if len(line) <= budget:
        return [line]
    return [line[i : i + budget] for i in range(0, len(line), budget)]
