"""OpenAI-compatible LLM client."""

from typing import Any, AsyncIterator

import openai

from src.config import get_config


class LLMClient:
    """OpenAI-compatible LLM client for chat completions."""

    def __init__(self) -> None:
        """Initialize the LLM client with configuration."""
        config = get_config()
        llm_config = config.llm

        self.client = openai.AsyncOpenAI(
            base_url=llm_config.get("base_url", "https://api.openai.com/v1"),
            api_key=llm_config.get("api_key", ""),
            timeout=llm_config.get("timeout", 120),
        )
        self.model = llm_config.get("model", "gpt-4o-mini")
        self.max_tokens = llm_config.get("max_tokens", 4096)
        self.context_window = llm_config.get("context_window", 128000)

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        """Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature.

        Returns:
            The assistant's response text.
        """
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=temperature,
        )

        return response.choices[0].message.content or ""

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Send a streaming chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature.

        Yields:
            Chunks of the assistant's response text.
        """
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=temperature,
            stream=True,
        )

        async for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                yield content
