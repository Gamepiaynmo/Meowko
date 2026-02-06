"""Builds LLM context from persona prompt, memories, and recent turns."""

import os
from pathlib import Path
from typing import Any

from src.config import get_config
from src.core.jsonl_store import JSONLStore


class ContextBuilder:
    """Builds context for LLM from persona and conversation history."""

    def __init__(self, data_dir: Path | None = None) -> None:
        """Initialize the context builder."""
        config = get_config()
        if data_dir is None:
            data_dir = Path(os.path.expanduser(config.paths["data_dir"]))
        self.data_dir = data_dir
        self.store = JSONLStore(data_dir)
        self.config = config

    def load_persona(self, persona_id: str) -> tuple[str, str]:
        """Load persona system prompt and nickname.

        Returns:
            Tuple of (system_prompt, nickname)
        """
        personas_dir = self.data_dir / self.config.paths["personas_dir"]
        persona_dir = personas_dir / persona_id

        # Load soul.md (system prompt)
        soul_path = persona_dir / "soul.md"
        if soul_path.exists():
            system_prompt = soul_path.read_text(encoding="utf-8")
        else:
            system_prompt = f"You are {persona_id}, a helpful assistant."

        # Load persona.yaml for nickname
        persona_yaml_path = persona_dir / "persona.yaml"
        nickname = persona_id
        if persona_yaml_path.exists():
            import yaml
            with open(persona_yaml_path, encoding="utf-8") as f:
                persona_config = yaml.safe_load(f)
            nickname = persona_config.get("nickname", persona_id)

        return system_prompt, nickname

    def build_context(
        self,
        user_id: int,
        persona_id: str = "alice",
    ) -> list[dict[str, Any]]:
        """Build the LLM context for a conversation.

        Returns:
            List of message dicts with 'role' and 'content' keys.
        """
        messages = []

        # 1. System prompt from persona
        system_prompt, _ = self.load_persona(persona_id)
        messages.append({"role": "system", "content": system_prompt})

        # 2. All previous conversation turns
        all_events = self.store.read_all(persona_id, user_id)

        for event in all_events:
            role = event.get("role")
            content = event.get("content")
            if role and content:
                messages.append({"role": role, "content": content})

        return messages

    def save_turn(
        self,
        user_id: int,
        user_message: str,
        assistant_message: str,
        persona_id: str = "alice",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cached_tokens: int = 0,
        cost: float = 0.0,
    ) -> None:
        """Save a user-assistant turn to the conversation log."""
        timestamp = __import__("datetime").datetime.now().isoformat()

        # Save user message
        self.store.append(
            persona_id,
            user_id,
            {
                "timestamp": timestamp,
                "role": "user",
                "content": user_message,
            },
        )

        # Save assistant message with token usage and cost
        self.store.append(
            persona_id,
            user_id,
            {
                "timestamp": timestamp,
                "role": "assistant",
                "content": assistant_message,
                "token_usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "cached_tokens": cached_tokens,
                },
                "cost": cost,
            },
        )
