"""Tests for TTS API key resolution from ~/.hermes/.env.

Covers the fix from #17140 / PR #XXXXX:
- TTS provider tools read API keys from ~/.hermes/.env when not in os.environ
- get_env_value() is used instead of os.getenv() for env var resolution
- env vars take priority over .env file (handled by get_env_value itself)

This follows the same pattern as the fix for credential_pool (PR #15920).
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _create_mock_env_file(home_path: Path, keys: dict) -> None:
    """Create a .env file in the given hermes home with specified keys."""
    env_file = home_path / ".env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in keys.items()))


@pytest.fixture
def isolated_hermes_home(tmp_path, monkeypatch):
    """Point HERMES_HOME at a temp dir and clear known TTS API key env vars.

    Also invalidates any cached get_env_value state by patching Path.home().
    """
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    # Clear all known TTS API key env vars so get_env_value falls through to .env
    for key in [
        "ELEVENLABS_API_KEY", "XAI_API_KEY", "MINIMAX_API_KEY",
        "MISTRAL_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)

    return home


class TestTTSEnvKeyResolution:
    """Test that TTS providers read API keys from ~/.hermes/.env."""

    @pytest.mark.parametrize("provider,key_var,key_value", [
        ("elevenlabs", "ELEVENLABS_API_KEY", "elevenlabs_test_key_123"),
        ("xai", "XAI_API_KEY", "xai_test_key_456"),
        ("minimax", "MINIMAX_API_KEY", "minimax_test_key_789"),
        ("mistral", "MISTRAL_API_KEY", "mistral_test_key_abc"),
        ("gemini", "GEMINI_API_KEY", "gemini_test_key_def"),
    ])
    def test_tts_provider_reads_from_env_file(
        self, isolated_hermes_home, provider, key_var, key_value
    ):
        """Verify TTS providers can read API keys from ~/.hermes/.env."""
        # Create .env file with the API key
        _create_mock_env_file(isolated_hermes_home, {key_var: key_value})

        # Import after .env file is created to avoid cached imports
        from tools import tts_tool

        # Verify get_env_value can read the key from .env
        from hermes_cli.config import get_env_value
        resolved_key = get_env_value(key_var)
        assert resolved_key == key_value, (
            f"get_env_value({key_var}) should return {key_value} from .env file"
        )

        # The actual test: verify that TTS provider function can access the key
        # We use check_tts_requirements function which reads the same env vars
        # The function returns True when at least one TTS provider is available
        with patch.object(tts_tool, "_import_edge_tts", side_effect=ImportError), \
             patch.object(tts_tool, "_import_elevenlabs", side_effect=ImportError), \
             patch.object(tts_tool, "_import_openai_client", side_effect=ImportError), \
             patch.object(tts_tool, "_check_neutts_available", return_value=False):
            # For elevenlabs and mistral, we also need to patch their import checks
            if provider in ("elevenlabs", "mistral"):
                # Revert the ImportError patch for the specific provider being tested
                if provider == "elevenlabs":
                    with patch.object(tts_tool, "_import_elevenlabs", side_effect=lambda: None):
                        from unittest.mock import MagicMock
                        # Mock the module as importable
                        with patch.dict("sys.modules", {"elevenlabs": MagicMock()}):
                            assert tts_tool.check_tts_requirements() is True, (
                                f"TTS requirements should be met when {key_var} is set in .env"
                            )
                else:  # mistral
                    with patch.object(tts_tool, "_import_mistral_client", side_effect=lambda: None):
                        from unittest.mock import MagicMock
                        # Mock the module as importable
                        with patch.dict("sys.modules", {"mistral_client": MagicMock()}):
                            assert tts_tool.check_tts_requirements() is True, (
                                f"TTS requirements should be met when {key_var} is set in .env"
                            )
            else:
                # For xai, minimax, gemini - no module import dependency
                assert tts_tool.check_tts_requirements() is True, (
                    f"TTS requirements should be met when {key_var} is set in .env"
                )

    def test_env_vars_take_priority_over_env_file(self, isolated_hermes_home, monkeypatch):
        """Verify that environment variables take priority over .env file."""
        # Create .env with a different key
        _create_mock_env_file(isolated_hermes_home, {"MINIMAX_API_KEY": "env_file_key"})

        # Set env var (higher priority)
        monkeypatch.setenv("MINIMAX_API_KEY", "env_var_key")

        from tools import tts_tool
        from hermes_cli.config import get_env_value

        # get_env_value should prefer env var over .env file
        resolved_key = get_env_value("MINIMAX_API_KEY")
        assert resolved_key == "env_var_key", (
            "Environment variable should take priority over .env file"
        )

    def test_missing_key_returns_empty_string(self, isolated_hermes_home):
        """Verify that missing keys return empty string or None."""
        # No .env file, no env vars
        from hermes_cli.config import get_env_value

        resolved_key = get_env_value("MINIMAX_API_KEY")
        # Should return empty string or None (not raise an error)
        assert resolved_key in ("", None), (
            "Missing API key should return empty string or None"
        )

    def test_gemini_fallback_to_google_api_key(self, isolated_hermes_home, monkeypatch):
        """Verify GEMINI_API_KEY falls back to GOOGLE_API_KEY."""
        # Create .env with GOOGLE_API_KEY only
        _create_mock_env_file(isolated_hermes_home, {"GOOGLE_API_KEY": "google_test_key"})

        # Both env vars should be unset in os.environ
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

        from tools import tts_tool
        from hermes_cli.config import get_env_value

        # get_env_value should read GOOGLE_API_KEY from .env
        google_key = get_env_value("GOOGLE_API_KEY")
        assert google_key == "google_test_key", (
            "Should read GOOGLE_API_KEY from .env file"
        )

        # Verify TTS provider requirements check passes via fallback
        # check_tts_requirements() returns True when at least one TTS provider is available
        with patch.object(tts_tool, "_import_edge_tts", side_effect=ImportError), \
             patch.object(tts_tool, "_import_elevenlabs", side_effect=ImportError), \
             patch.object(tts_tool, "_import_openai_client", side_effect=ImportError), \
             patch.object(tts_tool, "_check_neutts_available", return_value=False):
            assert tts_tool.check_tts_requirements() is True, (
                "TTS requirements should be met when GOOGLE_API_KEY is set in .env"
            )
