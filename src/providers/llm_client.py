"""OpenAI-compatible LLM client."""

import json
import logging
from datetime import datetime
from typing import Any

import openai

from src.config import get_config

logger = logging.getLogger("meowko.providers.llm")


class LLMResponse:
    """Response from LLM including text, token usage, and cost."""

    def __init__(
        self,
        content: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cached_tokens: int = 0,
        cost: float = 0.0,
    ):
        self.content = content
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.cached_tokens = cached_tokens
        self.cost = cost


class LLMStream:
    """Streaming LLM response — async-iterate for tokens, then read .response."""

    def __init__(self, stream: Any, client: "LLMClient") -> None:
        self._gen = self._iterate(stream, client)
        self.response: LLMResponse | None = None

    def __aiter__(self) -> Any:
        return self._gen

    async def _iterate(self, stream: Any, client: "LLMClient") -> Any:
        parts: list[str] = []
        usage = None
        async for chunk in stream:
            if chunk.usage:
                usage = chunk.usage
            if chunk.choices and chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                parts.append(token)
                yield token

        content = "".join(parts)
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        total_tokens = usage.total_tokens if usage else 0
        cached_tokens = 0
        if usage and hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details:
            cached_tokens = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0

        non_cached = prompt_tokens - cached_tokens
        cost = (
            (cached_tokens / 1_000_000) * client.cached_price
            + (non_cached / 1_000_000) * client.input_price
            + (completion_tokens / 1_000_000) * client.output_price
        )

        self.response = LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            cost=cost,
        )


class LLMClient:
    """OpenAI-compatible LLM client for chat completions."""

    _REQUEST_DIR_NAME = "llm_requests"
    _MAX_SAVED_REQUESTS = 5

    def __init__(self) -> None:
        config = get_config()
        model_config = config.get_model_config()

        self.client = openai.AsyncOpenAI(
            base_url=model_config["base_url"],
            api_key=model_config["api_key"],
            timeout=model_config["timeout"],
        )
        self.model = model_config["model"]
        self.max_tokens = model_config["max_tokens"]
        self.context_window = model_config["context_window"]

        self._request_dir = (
            config.data_dir / config.paths["cache_dir"] / self._REQUEST_DIR_NAME
        )

        # Load pricing (per 1M tokens)
        pricing = model_config["pricing"]
        self.input_price = pricing["input"]
        self.cached_price = pricing["cached"]
        self.output_price = pricing["output"]

    async def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
                      Content can be a string or a list of content blocks
                      (e.g., text + image_url for vision).
            temperature: Sampling temperature.

        Returns:
            LLMResponse with content and token usage.
        """
        self._save_request(messages, temperature)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=self.max_tokens,
            temperature=temperature,
        )

        content = response.choices[0].message.content or ""
        usage = response.usage

        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        total_tokens = usage.total_tokens if usage else 0

        # Check for cached tokens (OpenAI API v2+ provides this)
        cached_tokens = 0
        if usage and hasattr(usage, 'prompt_tokens_details') and usage.prompt_tokens_details:
            cached_tokens = getattr(usage.prompt_tokens_details, 'cached_tokens', 0) or 0

        # Calculate cost (prices are per 1M tokens)
        non_cached_input = prompt_tokens - cached_tokens
        cached_input_cost = (cached_tokens / 1_000_000) * self.cached_price
        non_cached_input_cost = (non_cached_input / 1_000_000) * self.input_price
        output_cost = (completion_tokens / 1_000_000) * self.output_price
        total_cost = cached_input_cost + non_cached_input_cost + output_cost

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            cost=total_cost,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
    ) -> LLMStream:
        """Start a streaming chat completion.

        Returns an LLMStream that yields tokens as they arrive.
        After iteration completes, access .response for usage stats.
        """
        self._save_request(messages, temperature)

        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=self.max_tokens,
            temperature=temperature,
            stream=True,
            stream_options={"include_usage": True},
        )

        return LLMStream(stream, self)

    def _save_request(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
    ) -> None:
        """Save the LLM request to cache, keeping only the 5 most recent."""
        try:
            self._request_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            path = self._request_dir / f"{timestamp}.json"
            payload = {
                "timestamp": datetime.now().isoformat(),
                "model": self.model,
                "temperature": temperature,
                "max_tokens": self.max_tokens,
                "messages": messages,
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

            # Remove oldest files beyond the limit
            files = sorted(self._request_dir.glob("*.json"))
            to_delete = files[: max(0, len(files) - self._MAX_SAVED_REQUESTS)]
            if to_delete:
                logger.debug(
                    "LLM request cache: %d files, deleting %d, keeping %d",
                    len(files), len(to_delete), len(files) - len(to_delete),
                )
            for old in to_delete:
                logger.debug("Deleting old request: %s", old.name)
                old.unlink()
        except Exception:
            logger.debug("Failed to save LLM request", exc_info=True)