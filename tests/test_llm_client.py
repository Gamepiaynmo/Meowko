"""Tests for LLMResponse."""

from src.providers.llm_client import LLMResponse


class TestLLMResponse:
    def test_default_values(self):
        r = LLMResponse(content="hello")
        assert r.content == "hello"
        assert r.prompt_tokens == 0
        assert r.completion_tokens == 0
        assert r.total_tokens == 0
        assert r.cached_tokens == 0
        assert r.cost == 0.0

    def test_custom_values(self):
        r = LLMResponse(
            content="response",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cached_tokens=20,
            cost=0.003,
        )
        assert r.prompt_tokens == 100
        assert r.completion_tokens == 50
        assert r.total_tokens == 150
        assert r.cached_tokens == 20
        assert r.cost == 0.003
