# Meowko

Multi-user, multi-persona Discord chatbot with text + voice support.

## Setup

Requires Python >=3.11. Uses `uv` for package management with a local `venv/`.

```sh
uv venv venv
source venv/bin/activate
uv pip install -e ".[dev]"
```

Copy `config.yaml.example` to `config.yaml` and fill in API keys. Runtime data lives in `~/.meowko` by default (configurable via `paths.data_dir`).

## Commands

```sh
# Run the bot
uv run meowko

# Lint
uv run ruff check src/

# Type check
uv run mypy src/

# Tests
uv run pytest tests/
```

## Project structure

```
src/
  config.py          — Singleton Config loader (config.yaml + DEFAULTS)
  main.py            — Entry point, logging setup, bot lifecycle
  core/
    context_builder.py — Builds LLM context (persona + memories + history)
    jsonl_store.py     — Append-only JSONL conversation storage
    memory_manager.py  — Hierarchical memory rollups and compaction
    scheduler.py       — Async scheduler for daily rollups
    user_state.py      — Per-user persona selection state
  discord/
    client.py          — MeowkoBot (discord.py commands.Bot subclass)
    commands.py        — Slash commands (/persona, etc.)
    handlers.py        — Message handler: attachments, LLM call, TTS/TTI
    voice.py           — Voice session manager, streaming STT/TTS
  media/
    audio.py           — PCM resampling, buffered audio source for playback
  providers/
    elevenlabs.py      — ElevenLabs TTS (batch + streaming)
    soniox.py          — Soniox STT (batch REST + streaming WebSocket)
    llm_client.py      — OpenAI-compatible chat completions client
    image_gen.py       — Text-to-image generation
    weather.py         — Open-Meteo weather API
config.yaml.example   — Annotated config template
pyproject.toml         — Project metadata, dependencies, tool config
```

## Architecture notes

- **Config** is a singleton (`Config()` / `get_config()`). Provider/model resolution uses `providers` list in config.yaml with `provider/model` format strings.
- **LLM responses** can contain `[tts]...[/tts]` and `[tti]...[/tti]` blocks. `MessageHandler._build_segments` parses these and generates media in parallel.
- **Voice pipeline**: Discord audio (48kHz stereo) → `AudioResampler.discord_to_stt` (mono) → `SonioxStreamingSTT` (WebSocket) → LLM → `ElevenLabsStreamingTTS` → `AudioResampler.tts_to_discord` (stereo) → `PCMStreamSource` playback.
- **Memory system**: Daily JSONL conversations are rolled up into markdown summaries by `MemoryManager`. `Scheduler` triggers daily rollups at `memory.rollup_time`. Context compaction happens when token count exceeds `context.compaction_threshold * context_window`.

## Code style

- Ruff with line-length 100, target Python 3.11
- No redundant docstrings on `__init__`, event handlers, or `cleanup` methods
- Keep comments minimal — only where logic isn't self-evident
