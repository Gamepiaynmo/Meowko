"""Validation helpers for persona IDs used in file-system paths."""

import re

_PERSONA_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def is_valid_persona_id(persona_id: str) -> bool:
    """Return True when persona_id is safe to use in path components."""
    return bool(persona_id) and bool(_PERSONA_ID_RE.fullmatch(persona_id))


def validate_persona_id(persona_id: str) -> str:
    """Validate persona_id and return it, or raise ValueError."""
    if not is_valid_persona_id(persona_id):
        raise ValueError(
            "Invalid persona_id. Use only letters, numbers, hyphens, and underscores."
        )
    return persona_id
