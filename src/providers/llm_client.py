"""OpenAI-compatible LLM client."""

from typing import Any, AsyncIterator

import openai

from src.config import get_config


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


class LLMClient:
    """OpenAI-compatible LLM client for chat completions."""

    def __init__(self) -> None:
        """Initialize the LLM client with configuration."""
        config = get_config()
        llm_config = config.llm

        self.client = openai.AsyncOpenAI(
            base_url=llm_config["base_url"],
            api_key=llm_config["api_key"],
            timeout=llm_config["timeout"],
        )
        self.model = llm_config["model"]
        self.max_tokens = llm_config["max_tokens"]
        self.context_window = llm_config["context_window"]

        # Load pricing (per 1M tokens)
        pricing = llm_config.get("pricing", {})
        self.input_price = pricing.get("input", 0.0)
        self.cached_price = pricing.get("cached", 0.0)
        self.output_price = pricing.get("output", 0.0)

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature.

        Returns:
            LLMResponse with content and token usage.
        """
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
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
            cached_tokens = getattr(usage.prompt_tokens_details, 'cached_tokens', 0)

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