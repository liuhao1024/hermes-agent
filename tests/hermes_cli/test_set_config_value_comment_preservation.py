"""Tests for set_config_value comment preservation (issue #63039)."""

import os
from unittest.mock import patch

import pytest

from hermes_cli.config import set_config_value


@pytest.fixture(autouse=True)
def _isolated_hermes_home(tmp_path):
    """Point HERMES_HOME at a temp dir so tests never touch real config."""
    env_file = tmp_path / ".env"
    env_file.touch()
    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
        yield tmp_path


def _read_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    return config_path.read_text() if config_path.exists() else ""


class TestCommentPreservation:
    """`hermes config set` must preserve comments and formatting."""

    def test_preserves_single_line_comment_after_key(self, _isolated_hermes_home):
        """A comment immediately after a key should survive set_config_value."""
        (_isolated_hermes_home / "config.yaml").write_text(
            "# My custom auxiliary setup\n"
            "auxiliary:\n"
            "  model: gpt-4o-mini  # Small model for auxiliary tasks\n"
        )

        set_config_value("auxiliary.model", "gpt-4o")

        config = _read_config(_isolated_hermes_home)
        assert "# My custom auxiliary setup" in config
        assert "# Small model for auxiliary tasks" in config
        assert "model: gpt-4o" in config

    def test_preserves_multiple_comments_in_section(self, _isolated_hermes_home):
        """Multiple comments in a section should all be preserved."""
        (_isolated_hermes_home / "config.yaml").write_text(
            "# Terminal configuration\n"
            "# Controls how the agent runs shell commands\n"
            "terminal:\n"
            "  backend: docker  # Use Docker for isolation\n"
            "  docker_image: python:3.12\n"
        )

        set_config_value("terminal.docker_image", "python:3.13")

        config = _read_config(_isolated_hermes_home)
        assert "# Terminal configuration" in config
        assert "# Controls how the agent runs shell commands" in config
        assert "# Use Docker for isolation" in config
        assert "backend: docker" in config
        assert "docker_image: python:3.13" in config

    def test_preserves_blank_lines(self, _isolated_hermes_home):
        """Blank lines between sections should be preserved."""
        (_isolated_hermes_home / "config.yaml").write_text(
            "model: claude-sonnet-4\n"
            "\n"
            "# Settings for TTS\n"
            "tts:\n"
            "  provider: openai\n"
        )

        set_config_value("tts.provider", "elevenlabs")

        config = _read_config(_isolated_hermes_home)
        assert "model: claude-sonnet-4" in config
        assert "\n\n" in config  # Two newlines → blank line preserved
        assert "# Settings for TTS" in config
        assert "tts:" in config

    def test_preserves_nested_comments(self, _isolated_hermes_home):
        """Comments in nested structures should be preserved."""
        (_isolated_hermes_home / "config.yaml").write_text(
            "platforms:\n"
            "  telegram:\n"
            "    # Token from @BotFather\n"
            "    bot_token: xxx\n"
            "    # Only allow these users\n"
            "    allowlist:\n"
            "      - alice\n"
        )

        set_config_value("platforms.telegram.bot_token", "yyy")

        config = _read_config(_isolated_hermes_home)
        assert "# Token from @BotFather" in config
        assert "# Only allow these users" in config
        assert "bot_token: yyy" in config

    def test_list_index_path_fallback_preserves_structure(self, _isolated_hermes_home):
        """List index paths (e.g., custom_providers.0.api_key) use PyYAML fallback
        and lose comments, but the list structure must be preserved."""
        (_isolated_hermes_home / "config.yaml").write_text(
            "custom_providers:\n"
            "  # Provider A - OpenAI-compatible\n"
            "  - name: provider-a\n"
            "    api_key: old-a\n"
            "    base_url: https://a.example.com\n"
            "  # Provider B\n"
            "  - name: provider-b\n"
            "    api_key: old-b\n"
            "    base_url: https://b.example.com\n"
        )

        set_config_value("custom_providers.0.api_key", "new-a")

        import yaml
        reloaded = yaml.safe_load(_read_config(_isolated_hermes_home))

        # Structure must be preserved (regression test for #17876)
        assert isinstance(reloaded["custom_providers"], list)
        assert len(reloaded["custom_providers"]) == 2
        assert reloaded["custom_providers"][0]["api_key"] == "new-a"
        assert reloaded["custom_providers"][1]["name"] == "provider-b"

        # Comments are lost (PyYAML limitation for list paths)
        config = _read_config(_isolated_hermes_home)
        assert "# Provider A" not in config  # Lost, expected