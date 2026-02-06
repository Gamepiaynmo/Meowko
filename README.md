# Meowko

A multi-user, multi-persona Discord chatbot with text and voice support.

## Features

- **Text Channels**: Text messages, image attachments, audio transcription
- **Voice Channels**: Real-time streaming STT/TTS with ElevenLabs
- **Multi-Persona**: Fixed persona catalog with per-user memory isolation
- **Memory Hierarchy**: Daily → Monthly → Yearly markdown rollups
- **Tools**: Brave web search and fetch
- **File-Only Persistence**: No database required

## Quick Start

1. Install dependencies:
   ```bash
   pip install -e .
   ```

2. Copy `config.yaml.example` to `config.yaml` and fill in your API keys:
   ```bash
   cp config.yaml.example config.yaml
   # Edit config.yaml with your API keys
   ```

3. Create a persona in `~/.meowko/personas/` (see example below)

4. Run the bot:
   ```bash
   meowko
   ```

## Project Structure

```
meowko/
├── src/              # Source code
│   ├── discord/      # Discord client, commands, handlers, voice
│   ├── core/         # Router, context, memory, scheduler
│   ├── providers/    # LLM, ElevenLabs, Brave
│   └── media/        # Audio processing
├── pyproject.toml    # Project configuration
├── config.yaml       # Bot configuration (created from example)
└── config.yaml.example  # Configuration template
```

## Data Directory

Runtime data is stored in `~/.meowko/`:

```
~/.meowko/
├── personas/         # Persona definitions
├── state/            # User state (active persona)
├── conversations/    # JSONL conversation logs
├── memories/         # Markdown memory files
├── cache/attachments/# Cached attachment files
└── logs/             # Log files
```

## Persona Format

Create `~/.meowko/personas/<persona_id>/persona.yaml`:

```yaml
id: alice
nickname: Alice
voice_id: eleven_voice_123
avatar: assets/avatar.png
```

And `~/.meowko/personas/<persona_id>/soul.md` with the system prompt.

## Commands

- `/persona list` - List available personas
- `/persona set <persona_id>` - Switch to a persona
- `/persona show` - Show current persona
