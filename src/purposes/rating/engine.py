from __future__ import annotations

import json
import re
from dataclasses import dataclass


# Tunables for the rating math.
RATING_K = 1.4          # how hard a beat/miss of expectation swings the rating
MAX_ABS_DELTA = 80      # clamp so a single submission can't wildly swing
NEUTRAL_SCORE = 50      # the score an "average" 1500 player is expected to hit

# Weights for the 0..10 sub-scores; must sum to 1.0.
_WEIGHTS = {"correctness": 0.5, "efficiency": 0.3, "readability": 0.2}

# Reuse the formatter's fence shape so we tolerate a model that wraps its JSON
# in a ```json ... ``` block instead of replying with bare JSON.
_FENCE_RE = re.compile(r"```[^\n`]*\n(.*?)```", re.DOTALL)
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


REVIEW_SYSTEM_PROMPT = (
    "You are a strict but fair senior code reviewer scoring a submission for a "
    "Codeforces-style rated practice session. Judge the code on three axes, each "
    "from 0 to 10:\n"
    "- correctness: does it solve the problem and handle edge cases?\n"
    "- efficiency: time and space complexity versus the optimal approach.\n"
    "- readability: naming, structure, comments, idiomatic style.\n\n"
    "Reply with ONLY a single JSON object and nothing else, in exactly this shape:\n"
    '{"correctness": <0-10>, "efficiency": <0-10>, "readability": <0-10>, '
    '"verdict": "<short label, e.g. Accepted, Suboptimal, Buggy>", '
    '"summary": "<one concise sentence of feedback>"}\n'
    "Do not include markdown, code fences, or any prose outside the JSON object."
)


class ReviewParseError(ValueError):
    """Raised when the model's review cannot be parsed into scores."""


@dataclass(frozen=True)
class ReviewResult:
    correctness: int
    efficiency: int
    readability: int
    verdict: str
    summary: str


def build_review_messages(code: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": f"Review and score this submission:\n{code.strip()}"},
    ]


def parse_review(content: str) -> ReviewResult:
    """Parse the model's reply into a ReviewResult, tolerating fenced/extra text."""
    payload = _extract_json_object(content)
    if payload is None:
        raise ReviewParseError("no JSON object found in the review response")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ReviewParseError(f"review response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ReviewParseError("review JSON was not an object")

    try:
        return ReviewResult(
            correctness=_clamp_score(data["correctness"]),
            efficiency=_clamp_score(data["efficiency"]),
            readability=_clamp_score(data["readability"]),
            verdict=str(data.get("verdict", "Reviewed")).strip() or "Reviewed",
            summary=str(data.get("summary", "")).strip(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewParseError(f"review JSON missing or invalid scores: {exc}") from exc


def score_of(result: ReviewResult) -> int:
    """Collapse the three sub-scores into a single 0..100 quality score."""
    weighted = (
        result.correctness * _WEIGHTS["correctness"]
        + result.efficiency * _WEIGHTS["efficiency"]
        + result.readability * _WEIGHTS["readability"]
    )
    return round(weighted * 10)


def compute_delta(current_rating: int, score: int) -> int:
    """Codeforces-flavored rating delta from a 0..100 quality score.

    The expected score rises with the user's current rating, so the same quality
    earns less as you climb. Beating expectation gains points; missing it loses
    them. The result is clamped to +/- MAX_ABS_DELTA.
    """
    expected = _clamp(NEUTRAL_SCORE + (current_rating - 1500) / 20, 10, 95)
    delta = round((score - expected) * RATING_K)
    return int(_clamp(delta, -MAX_ABS_DELTA, MAX_ABS_DELTA))


# (lower_bound_inclusive, name, color) — Codeforces-like tiers, ascending.
_TIERS: tuple[tuple[int, str, int], ...] = (
    (0, "Newbie", 0x808080),
    (1200, "Pupil", 0x008000),
    (1400, "Specialist", 0x03A89E),
    (1600, "Expert", 0x0000FF),
    (1900, "Candidate Master", 0xAA00AA),
    (2100, "Master", 0xFF8C00),
    (2300, "Grandmaster", 0xFF0000),
)


def tier_for(rating: int) -> tuple[str, int]:
    """Return (tier_name, color_int) for a rating."""
    name, color = _TIERS[0][1], _TIERS[0][2]
    for threshold, tier_name, tier_color in _TIERS:
        if rating >= threshold:
            name, color = tier_name, tier_color
        else:
            break
    return name, color


def _extract_json_object(content: str) -> str | None:
    fenced = _FENCE_RE.search(content)
    candidate = fenced.group(1) if fenced else content
    match = _OBJECT_RE.search(candidate)
    return match.group(0) if match else None


def _clamp_score(value: object) -> int:
    return int(_clamp(round(float(value)), 0, 10))  # type: ignore[arg-type]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
