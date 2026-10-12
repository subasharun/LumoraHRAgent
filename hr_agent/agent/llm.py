"""Thin wrapper around any OpenAI-compatible chat-completions API with tool calling.

Default provider: Groq's free tier (https://console.groq.com), model openai/gpt-oss-120b.
Other free options that speak the same API: OpenRouter ":free" models, Google Gemini's
OpenAI-compatible endpoint, or a local Ollama server. Switch with LLM_BASE_URL / LLM_MODEL.

Free tiers have tight rate limits, so this wrapper retries briefly on HTTP 429 and
otherwise raises `LLMUnavailable`, which makes the orchestrator fall back to the
deterministic offline planner instead of failing the user's request.
"""

from __future__ import annotations

import asyncio
import logging
import time

from ..config import Settings

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """Raised when the LLM cannot be used for this request (no key, rate limit, outage)."""


class LLMClient:
    def __init__(self, settings: Settings):
        if not settings.llm_api_key and "localhost" not in settings.llm_base_url and "127.0.0.1" not in settings.llm_base_url:
            raise LLMUnavailable("LLM_API_KEY is not set")
        from openai import AsyncOpenAI  # imported lazily so the offline mode has no hard dependency

        self.settings = settings
        self.model = settings.llm_model
        self._client = AsyncOpenAI(
            api_key=settings.llm_api_key or "not-needed",
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_s,
            max_retries=0,  # we handle retries ourselves so we can respect free-tier limits
        )
        self.total_tokens = 0

    async def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """One chat-completion call. Returns {"content", "tool_calls", "usage", "latency_ms"}."""
        from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

        kwargs = {"model": self.model, "messages": messages, "temperature": self.settings.llm_temperature,
                  "seed": self.settings.seed, "max_tokens": 1200}
        if tools:
            kwargs.update(tools=tools, tool_choice="auto", parallel_tool_calls=True)
        if "gpt-oss" in self.model and self.settings.llm_reasoning_effort:
            kwargs["extra_body"] = {"reasoning_effort": self.settings.llm_reasoning_effort}

        for attempt in range(3):
            started = time.perf_counter()
            try:
                resp = await self._client.chat.completions.create(**kwargs)
                msg = resp.choices[0].message
                usage = resp.usage.total_tokens if resp.usage else 0
                self.total_tokens += usage
                return {
                    "content": msg.content or "",
                    "tool_calls": [
                        {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or "{}"}
                        for tc in (msg.tool_calls or [])
                    ],
                    "usage": usage,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            except RateLimitError as exc:
                wait = _retry_after(exc)
                if attempt < 2 and wait <= 20:
                    log.warning("LLM rate limited; retrying in %.1fs", wait)
                    await asyncio.sleep(wait)
                    continue
                raise LLMUnavailable(f"rate limited by provider ({exc.status_code})") from exc
            except APIStatusError as exc:
                # Groq returns 400 "tool_use_failed" when the model emits a malformed tool call.
                if exc.status_code == 400 and "tool_use_failed" in str(exc) and attempt < 2:
                    log.warning("Malformed tool call from model; retrying")
                    continue
                if exc.status_code >= 500 and attempt < 2:
                    await asyncio.sleep(1.5)
                    continue
                raise LLMUnavailable(f"provider error {exc.status_code}: {str(exc)[:200]}") from exc
            except (APIConnectionError, APITimeoutError) as exc:
                if attempt < 1:
                    continue
                raise LLMUnavailable(f"provider unreachable: {exc}") from exc
        raise LLMUnavailable("LLM call failed after retries")


def _retry_after(exc) -> float:
    try:
        value = exc.response.headers.get("retry-after")
        return float(value) if value else 2.0
    except Exception:
        return 2.0
