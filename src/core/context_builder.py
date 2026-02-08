"""Builds LLM context from persona prompt, memories, and recent turns."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from zoneinfo import ZoneInfo

from src.config import get_config
from src.core.jsonl_store import JSONLStore
from src.providers.weather import get_weather, weather_code_to_description

if TYPE_CHECKING:
    from src.core.memory_manager import MemoryManager

logger = logging.getLogger("meowko.core.context")


class ContextBuilder:
    """Builds context for LLM from persona and conversation history."""

    def __init__(self, data_dir: Path | None = None) -> None:
        config = get_config()
        if data_dir is None:
            data_dir = config.data_dir
        self.data_dir = data_dir
        self.store = JSONLStore(data_dir)
        self.config = config
        self._memory_manager: MemoryManager | None = None

    @property
    def memory_manager(self) -> MemoryManager:
        """Lazy-init MemoryManager to avoid circular imports."""
        if self._memory_manager is None:
            from src.core.memory_manager import MemoryManager
            self._memory_manager = MemoryManager(self.data_dir)
        return self._memory_manager

    def load_persona(self, persona_id: str) -> dict[str, str | None]:
        """Load persona system prompt, nickname, and voice_id.

        Returns:
            Dict with keys: prompt, nickname, voice_id.
        """
        personas_dir = self.data_dir / self.config.paths["personas_dir"]
        persona_dir = personas_dir / persona_id

        # Load soul.md (system prompt)
        soul_path = persona_dir / "soul.md"
        if soul_path.exists():
            system_prompt = soul_path.read_text(encoding="utf-8")
        else:
            system_prompt = f"You are {persona_id}, a helpful assistant."

        # Load persona.yaml for nickname and voice_id
        persona_yaml_path = persona_dir / "persona.yaml"
        nickname = persona_id
        voice_id = None
        if persona_yaml_path.exists():
            with open(persona_yaml_path, encoding="utf-8") as f:
                persona_config = yaml.safe_load(f)
            nickname = persona_config.get("nickname", persona_id)
            voice_id = persona_config.get("voice_id")

        return {"prompt": system_prompt, "nickname": nickname, "voice_id": voice_id}

    async def build_context(
        self,
        user_id: int,
        persona_id: str,
    ) -> list[dict[str, Any]]:
        """Build the LLM context for a conversation.

        Returns:
            List of message dicts with 'role' and 'content' keys.
        """
        messages = []

        # 1. Shared prompts + persona system prompt (single system message)
        system_parts = self._load_shared_prompts()
        persona = self.load_persona(persona_id)
        system_parts.append(persona["prompt"] or "")
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})

        # 2. Inject memories
        scope_id = f"{persona_id}-{user_id}"
        memory_text = self.memory_manager.read_all_memories(scope_id)
        if memory_text:
            messages.append({
                "role": "system",
                "content": f"# 你与他的记忆\n\n{memory_text}",
            })

        # 3. All previous conversation turns
        all_events = self.store.read_all(persona_id, user_id)

        # 4. If conversation is empty, add date and weather context
        if not all_events:
            context_info = await self._build_context_info()
            if context_info:
                messages.append({"role": "system", "content": context_info})
                # Save context info to conversation log
                self._save_context_info(user_id, persona_id, context_info)

        for event in all_events:
            role = event.get("role")
            content = event.get("content")
            if role and content:
                messages.append({"role": role, "content": content})

        # 5. Check compaction threshold
        context_window = self.config.get_model_config()["context_window"]
        threshold = self.config.context.get("compaction_threshold", 0.9)
        all_text = " ".join(m.get("content", "") for m in messages if isinstance(m.get("content"), str))

        from src.core.memory_manager import estimate_tokens
        if estimate_tokens(all_text) > context_window * threshold:
            tz = ZoneInfo(self.config.memory.get("timezone", "UTC"))
            today = datetime.now(tz).date()
            logger.info("Context exceeds threshold, compacting conversation for %s", scope_id)
            await self.memory_manager.compact_conversation(scope_id, today)

            return await self.build_context(user_id, persona_id)

        return messages

    def _save_context_info(
        self,
        user_id: int,
        persona_id: str,
        context_info: str,
    ) -> None:
        """Save context info (date/weather) to conversation log."""
        timestamp = datetime.now().isoformat()
        self.store.append(
            persona_id,
            user_id,
            {
                "timestamp": timestamp,
                "role": "system",
                "content": context_info,
                "type": "context_info",
            },
        )

    def _load_shared_prompts(self) -> list[str]:
        """Load shared system prompt files listed in config.prompts."""
        names = self.config.get("prompts", [])
        if not names:
            return []

        prompts_dir = self.data_dir / "prompts"
        results = []
        for name in names:
            path = prompts_dir / name
            if path.exists():
                results.append(path.read_text(encoding="utf-8"))
            else:
                logger.warning("Shared prompt not found: %s", path)
        return results

    async def _build_context_info(self) -> str:
        """Build context info with current date and weather.

        Returns:
            Formatted context info string.
        """
        # Get current date
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d %A")

        # Get weather
        try:
            weather = await get_weather()
            weather_desc = weather_code_to_description(weather["weather_code"])
            temp_max = weather["temp_max"]
            temp_min = weather["temp_min"]
            weather_str = f"{weather_desc}, {temp_min}°C~{temp_max}°C"
        except Exception:
            weather_str = "Unknown"

        # Get template from config
        template = self.config.context["info_template"]

        return str(template.format(date=date_str, weather=weather_str))

    def save_cache_file(
        self,
        persona_id: str,
        user_id: int,
        filename: str,
        data: bytes,
    ) -> str:
        """Save bytes to the cache directory and return the relative path.

        Path format: cache/{persona_id}-{user_id}/{date}/{HH-MM-SS}-{uuid}.{ext}

        Returns:
            Path relative to data_dir (e.g. "cache/meowko-123/2026-02-07/14-30-52-a1b2c3d4.jpg").
        """
        cache_dir = self.config.paths["cache_dir"]
        scope = f"{persona_id}-{user_id}"
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H-%M-%S")
        short_id = uuid.uuid4().hex[:8]
        ext = Path(filename).suffix
        rel = Path(cache_dir) / scope / date_str / f"{time_str}-{short_id}{ext}"

        abs_path = self.data_dir / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(data)

        logger.debug("Cached %d bytes → %s", len(data), rel)
        return str(rel)

    def save_turn(
        self,
        user_id: int,
        user_message: str,
        assistant_message: str,
        persona_id: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cached_tokens: int = 0,
        cost: float = 0.0,
        user_attachments: list[dict[str, str]] | None = None,
        assistant_attachments: list[dict[str, str]] | None = None,
    ) -> None:
        """Save a user-assistant turn to the conversation log."""
        timestamp = datetime.now().isoformat()

        # Save user message
        user_event: dict[str, Any] = {
            "timestamp": timestamp,
            "role": "user",
            "content": user_message,
        }
        if user_attachments:
            user_event["attachments"] = user_attachments

        self.store.append(persona_id, user_id, user_event)

        # Save assistant message with token usage and cost
        assistant_event: dict[str, Any] = {
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
        }
        if assistant_attachments:
            assistant_event["attachments"] = assistant_attachments

        self.store.append(persona_id, user_id, assistant_event)
