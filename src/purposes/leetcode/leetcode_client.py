from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


class LeetCodeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProblemInfo:
    frontend_id: str
    title: str
    slug: str
    difficulty: str


class LeetCodeClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
                "Origin": "https://leetcode.com",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def resolve_problem(self, problem: str) -> ProblemInfo:
        parsed = _parse_problem_input(problem)
        if parsed[0] == "slug":
            return await self._fetch_problem_by_slug(parsed[1])
        return await self._fetch_problem_by_number(parsed[1])

    async def _fetch_problem_by_slug(self, slug: str) -> ProblemInfo:
        query = """
        query question($titleSlug: String!) {
          question(titleSlug: $titleSlug) {
            questionFrontendId
            title
            titleSlug
            difficulty
          }
        }
        """
        data = await self._graphql(query, {"titleSlug": slug}, referer=f"https://leetcode.com/problems/{slug}/")
        question = data.get("question")
        if not question:
            raise LeetCodeError(f"Could not find LeetCode problem slug `{slug}`.")
        return ProblemInfo(
            frontend_id=question["questionFrontendId"],
            title=question["title"],
            slug=question["titleSlug"],
            difficulty=question["difficulty"],
        )

    async def _fetch_problem_by_number(self, number: str) -> ProblemInfo:
        query = """
        query questionList($filters: QuestionListFilterInput) {
          questionList(categorySlug: "all-code-essentials", limit: 20, skip: 0, filters: $filters) {
            data {
              questionFrontendId
              title
              titleSlug
              difficulty
            }
          }
        }
        """
        data = await self._graphql(query, {"filters": {"searchKeywords": number}}, referer="https://leetcode.com/problemset/")
        questions = data.get("questionList", {}).get("data", [])
        for question in questions:
            if question.get("questionFrontendId") == number:
                return ProblemInfo(
                    frontend_id=question["questionFrontendId"],
                    title=question["title"],
                    slug=question["titleSlug"],
                    difficulty=question["difficulty"],
                )
        raise LeetCodeError(f"Could not find LeetCode problem number `{number}`.")

    async def _graphql(self, query: str, variables: dict[str, object], referer: str) -> dict[str, object]:
        try:
            response = await self._client.post(
                "https://leetcode.com/graphql",
                json={"query": query, "variables": variables},
                headers={"Referer": referer},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LeetCodeError(f"LeetCode returned HTTP {exc.response.status_code}.") from exc
        except httpx.HTTPError as exc:
            raise LeetCodeError(f"LeetCode request failed: {exc.__class__.__name__}.") from exc

        payload = response.json()
        if payload.get("errors"):
            message = payload["errors"][0].get("message", "unknown GraphQL error")
            raise LeetCodeError(f"LeetCode GraphQL error: {message}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise LeetCodeError("LeetCode returned an unexpected response format.")
        return data


def _parse_problem_input(problem: str) -> tuple[str, str]:
    value = problem.strip()
    if not value:
        raise LeetCodeError("Problem must be a number, slug, or LeetCode problem URL.")

    if value.isdigit():
        return ("number", value)

    parsed = urlparse(value)
    if parsed.netloc and "leetcode.com" in parsed.netloc:
        match = re.search(r"/problems/([^/]+)/?", parsed.path)
        if not match:
            raise LeetCodeError("LeetCode URL must contain `/problems/<slug>`.")
        return ("slug", match.group(1))

    slug = value.strip("/").split("/")[-1]
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        raise LeetCodeError("Problem slug can only contain lowercase letters, numbers, and hyphens.")
    return ("slug", slug)
