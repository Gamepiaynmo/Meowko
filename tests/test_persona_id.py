"""Tests for persona ID validation helpers."""

import pytest

from src.core.persona_id import is_valid_persona_id, validate_persona_id


class TestPersonaIdValidation:
    def test_valid_persona_ids(self) -> None:
        assert is_valid_persona_id("meowko")
        assert is_valid_persona_id("cat-girl_2")

    def test_invalid_persona_ids(self) -> None:
        assert not is_valid_persona_id("")
        assert not is_valid_persona_id("../x")
        assert not is_valid_persona_id("x/y")
        assert not is_valid_persona_id("x y")

    def test_validate_raises_for_invalid(self) -> None:
        with pytest.raises(ValueError, match="Invalid persona_id"):
            validate_persona_id("..")
