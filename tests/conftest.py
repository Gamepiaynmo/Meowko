"""Shared test fixtures."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.config import Config, DEFAULTS


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset Config singleton between tests."""
    Config._instance = None
    yield
    Config._instance = None


@pytest.fixture()
def tmp_data_dir(tmp_path):
    """Create a temporary data directory with required subdirs."""
    for sub in ("conversations", "memories", "state", "cache", "personas", "prompts", "logs"):
        (tmp_path / sub).mkdir()
    return tmp_path


@pytest.fixture()
def config_file(tmp_path):
    """Create a minimal config.yaml and load it into the Config singleton."""
    cfg = {
        "default_persona": "test-persona",
        "providers": [
            {
                "name": "testprov",
                "base_url": "http://localhost:1234",
                "api_key": "test-key",
                "models": [
                    {
                        "name": "test-model",
                        "context_window": 8000,
                        "max_tokens": 512,
                        "pricing": {"input": 1.0, "cached": 0.5, "output": 2.0},
                    }
                ],
            }
        ],
        "llm": {"model": "testprov/test-model", "timeout": 30},
        "memory": {"rollup_time": "03:00", "timezone": "UTC"},
        "paths": {
            "data_dir": str(tmp_path / "data"),
            "personas_dir": "personas",
            "state_dir": "state",
            "conversations_dir": "conversations",
            "memories_dir": "memories",
            "cache_dir": "cache",
            "logs_dir": "logs",
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(cfg), encoding="utf-8")

    config = Config()
    config.load(path)

    # Ensure data subdirs exist
    data_dir = config.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("conversations", "memories", "state", "cache", "personas", "prompts", "logs"):
        (data_dir / sub).mkdir(exist_ok=True)

    return path
