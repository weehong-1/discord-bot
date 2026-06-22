from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from ..config import Settings
from .models import MODEL_ALLOWLIST


LOGGER = logging.getLogger(__name__)


class AIProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class AIResponse:
    content: str
    provider: str
    model: str
    used_fallback: bool = False


class AIClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0))

    async def close(self) -> None:
        await self._client.aclose()

    async def complete(self, messages: list[dict[str, str]], model_key: str | None = None) -> AIResponse:
        selected_model_key = model_key or self._settings.openrouter_model_key
        if selected_model_key not in MODEL_ALLOWLIST:
            allowed = ", ".join(sorted(MODEL_ALLOWLIST))
            raise AIProviderError(f"Unsupported model. Choose one of: {allowed}")

        openrouter_model = MODEL_ALLOWLIST[selected_model_key]
        try:
            content = await self._openrouter_complete(messages, openrouter_model)
            return AIResponse(content=content, provider="OpenRouter", model=openrouter_model)
        except Exception as exc:
            LOGGER.warning("OpenRouter request failed, trying DeepSeek fallback: %s", exc)

        if not self._settings.deepseek_api_key:
            raise AIProviderError("OpenRouter failed and DEEPSEEK_API_KEY is not configured for fallback.")

        content = await self._deepseek_complete(messages)
        return AIResponse(
            content=content,
            provider="DeepSeek",
            model=self._settings.deepseek_model,
            used_fallback=True,
        )

    async def _openrouter_complete(self, messages: list[dict[str, str]], model: str) -> str:
        headers = {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "X-Title": self._settings.openrouter_app_name,
        }
        if self._settings.openrouter_site_url:
            headers["HTTP-Referer"] = self._settings.openrouter_site_url

        return await self._post_chat_completion(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            payload={
                "model": model,
                "messages": messages,
                "temperature": 0.4,
            },
            provider="OpenRouter",
        )

    async def _deepseek_complete(self, messages: list[dict[str, str]]) -> str:
        headers = {
            "Authorization": f"Bearer {self._settings.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        return await self._post_chat_completion(
            url="https://api.deepseek.com/chat/completions",
            headers=headers,
            payload={
                "model": self._settings.deepseek_model,
                "messages": messages,
                "temperature": 0.4,
            },
            provider="DeepSeek",
        )

    async def _post_chat_completion(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        provider: str,
    ) -> str:
        try:
            response = await self._client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = _safe_error_detail(exc.response)
            raise AIProviderError(f"{provider} returned HTTP {exc.response.status_code}: {detail}") from exc
        except httpx.HTTPError as exc:
            raise AIProviderError(f"{provider} request failed: {exc.__class__.__name__}") from exc

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError(f"{provider} returned an unexpected response format") from exc

        if not isinstance(content, str) or not content.strip():
            raise AIProviderError(f"{provider} returned an empty response")
        return content.strip()


def _safe_error_detail(response: httpx.Response) -> str:
    text = response.text.strip().replace("\n", " ")
    if not text:
        return "no response body"
    return text[:300]
