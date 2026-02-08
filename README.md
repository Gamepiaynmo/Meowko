# Meowko

A multi-user, multi-persona Discord chatbot with text and voice support.

## Features

- **Text Channels**: Text messages, image attachments, audio transcription via Soniox STT
- **Voice Channels**: Real-time streaming STT/TTS with barge-in support and auto-join/auto-leave
- **Multi-Persona**: Per-user persona selection with isolated conversation history and memory
- **Text-to-Speech**: ElevenLabs TTS with per-persona voice IDs (batch + streaming)
- **Text-to-Image**: OpenAI-compatible image generation via `[tti]...[/tti]` blocks
- **Memory Hierarchy**: Daily → Weekly → Monthly → Seasonal → Yearly markdown rollups with automatic compaction
- **Weather Context**: Open-Meteo weather injected into conversation context on new sessions
- **Shared Prompts**: Global system prompt files prepended to all persona prompts
- **File-Only Persistence**: No database required — JSONL conversations, markdown memories

## Quick Start

Requires Python >=3.11 and [`uv`](https://docs.astral.sh/uv/).

1. Set up the environment:
   ```bash
   uv venv venv
   source venv/bin/activate
   uv pip install -e ".[dev]"
   ```

2. Copy `config.yaml.example` to `config.yaml` and fill in your API keys:
   ```bash
   cp config.yaml.example config.yaml
   ```

3. Create a persona in `~/.meowko/personas/` (see Persona Format below)

4. Run the bot:
   ```bash
   uv run meowko
   ```

## Project Structure

```
src/
├── config.py            # Singleton config loader (config.yaml + defaults)
├── main.py              # Entry point, logging setup, bot lifecycle
├── core/
│   ├── context_builder.py  # Builds LLM context (persona + memories + history)
│   ├── jsonl_store.py      # Append-only JSONL conversation storage
│   ├── memory_manager.py   # Hierarchical memory rollups and compaction
│   ├── scheduler.py        # Async scheduler for daily rollups
│   └── user_state.py       # Per-user persona selection state
├── discord/
│   ├── client.py           # MeowkoBot (discord.py commands.Bot subclass)
│   ├── commands.py         # Slash commands (/persona, /join, /leave, etc.)
│   ├── handlers.py         # Message handler: attachments, LLM call, TTS/TTI
│   └── voice.py            # Voice session manager, streaming STT/TTS
├── media/
│   └── audio.py            # PCM resampling, buffered audio source for playback
└── providers/
    ├── llm_client.py       # OpenAI-compatible chat completions client
    ├── elevenlabs.py       # ElevenLabs TTS (batch + streaming)
    ├── soniox.py           # Soniox STT (batch REST + streaming WebSocket)
    ├── image_gen.py        # Text-to-image generation
    └── weather.py          # Open-Meteo weather API
```

## Data Directory

Runtime data is stored in `~/.meowko/` (configurable via `paths.data_dir`):

```
~/.meowko/
├── personas/         # Persona definitions (persona.yaml + soul.md)
├── prompts/          # Shared system prompt files (referenced in config.yaml)
├── state/            # User state (active persona per user)
├── conversations/    # JSONL conversation logs (per scope per day)
├── memories/         # Markdown memory files (daily/weekly/monthly/seasonal/yearly)
├── cache/            # Cached attachments and generated media
└── logs/             # Log files
```

## Persona Format

Create `~/.meowko/personas/<persona_id>/persona.yaml`:

```yaml
id: alice
nickname: Alice
voice_id: eleven_voice_123   # ElevenLabs voice ID for TTS
```

And `~/.meowko/personas/<persona_id>/soul.md` with the system prompt.

## Commands

- `/persona list` — List available personas (marks active one)
- `/persona set <persona_id>` — Switch to a persona
- `/join` — Join your current voice channel
- `/leave` — Leave the voice channel
- `/compact` — Manually compact current conversation into memory
- `/rewind` — Remove the last exchange from conversation history
