"""Tests for UserState per-user persona persistence."""

from src.core.user_state import UserState


class TestUserState:
    def test_get_default_persona(self, config_file):
        state = UserState()
        # No state saved yet — falls back to config default
        assert state.get_persona_id(12345) == "test-persona"

    def test_set_and_get_persona(self, config_file):
        state = UserState()
        state.set_persona_id(42, "catgirl")
        assert state.get_persona_id(42) == "catgirl"

    def test_overwrite_persona(self, config_file):
        state = UserState()
        state.set_persona_id(42, "first")
        state.set_persona_id(42, "second")
        assert state.get_persona_id(42) == "second"

    def test_different_users_independent(self, config_file):
        state = UserState()
        state.set_persona_id(1, "persona_a")
        state.set_persona_id(2, "persona_b")
        assert state.get_persona_id(1) == "persona_a"
        assert state.get_persona_id(2) == "persona_b"

    def test_persistence_across_instances(self, config_file):
        state1 = UserState()
        state1.set_persona_id(99, "persistent")

        state2 = UserState()
        assert state2.get_persona_id(99) == "persistent"
