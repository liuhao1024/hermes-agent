#!/usr/bin/env python3
"""
Regression tests for Issue #36098: honcho self-hosted localhost setup silently fails.
Tests for:
1. Recursive apiKey fallback (hosts.hermes.<profile> -> hosts.hermes -> top-level -> env)
2. _is_local trap (trust config.api_key when non-empty regardless of localhost)
3. 60s default timeout
4. Non-empty error marker on dialectic_query failure
"""
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from plugins.memory.honcho.client import (
    HonchoClientConfig,
    _DEFAULT_HTTP_TIMEOUT,
    HOST,
)


class TestIssue36098RecursiveApiKeyFallback:
    """Test that apiKey falls back through hosts.hermes.<profile> -> hosts.hermes -> top-level -> env."""

    def test_api_key_fallback_to_default_block(self, tmp_path):
        """apiKey should fall back to hosts.hermes (default block) when profile block missing it."""
        config_file = tmp_path / "honcho.json"
        config_file.write_text(
            json.dumps({
                "apiKey": "top-level-key",
                "hosts": {
                    "hermes": {
                        "apiKey": "default-block-key",
                    },
                    "hermes_profile1": {
                        "workspace": "hermes",
                        # No apiKey — should inherit from default block
                    }
                }
            })
        )

        with patch("plugins.memory.honcho.client.resolve_config_path", return_value=config_file):
            with patch("plugins.memory.honcho.client.resolve_active_host", return_value="hermes_profile1"):
                config = HonchoClientConfig.from_global_config(host="hermes_profile1")
                # Should fallback to default block, not top-level
                assert config.api_key == "default-block-key", f"Expected 'default-block-key', got '{config.api_key}'"

    def test_api_key_fallback_order(self, tmp_path):
        """Full fallback chain: profile -> default -> top-level -> env."""
        config_file = tmp_path / "honcho.json"
        config_file.write_text(
            json.dumps({
                "apiKey": "top-level-key",
                "hosts": {
                    "hermes": {
                        "apiKey": "default-block-key",
                    },
                    "hermes_profile2": {
                        "apiKey": "profile-block-key",
                    }
                }
            })
        )

        with patch("plugins.memory.honcho.client.resolve_config_path", return_value=config_file):
            with patch("plugins.memory.honcho.client.resolve_active_host", return_value="hermes_profile2"):
                config = HonchoClientConfig.from_global_config(host="hermes_profile2")
                assert config.api_key == "profile-block-key"

    def test_api_key_fallback_env_var(self, tmp_path, monkeypatch):
        """Env var wins when no config has apiKey."""
        config_file = tmp_path / "honcho.json"
        config_file.write_text(
            json.dumps({
                "hosts": {
                    "hermes": {},
                    "hermes_profile3": {},
                }
            })
        )

        monkeypatch.setenv("HONCHO_API_KEY", "env-key")

        with patch("plugins.memory.honcho.client.resolve_config_path", return_value=config_file):
            with patch("plugins.memory.honcho.client.resolve_active_host", return_value="hermes_profile3"):
                config = HonchoClientConfig.from_global_config(host="hermes_profile3")
                assert config.api_key == "env-key"


class TestIssue36098IsLocalTrap:
    """Test that _is_local doesn't override config.api_key when non-empty."""

    def test_localhost_with_non_empty_api_key_from_default_block(self, tmp_path):
        """When baseUrl is localhost and api_key comes from default block, use it (not 'local')."""
        config_file = tmp_path / "honcho.json"
        config_file.write_text(
            json.dumps({
                "baseUrl": "http://localhost:8000",
                "apiKey": "top-level-key",
                "hosts": {
                    "hermes": {
                        "apiKey": "default-block-key",
                    },
                    "hermes_profile4": {
                        # No apiKey — should inherit from default block
                    }
                }
            })
        )

        with patch("plugins.memory.honcho.client.resolve_config_path", return_value=config_file):
            with patch("plugins.memory.honcho.client.resolve_active_host", return_value="hermes_profile4"):
                config = HonchoClientConfig.from_global_config(host="hermes_profile4")
                # api_key should be "default-block-key" (inherited), not replaced with "local"
                assert config.api_key == "default-block-key"

                # Verify the client uses this key, not "local"
                from plugins.memory.honcho.client import get_honcho_client
                client = get_honcho_client(config)
                assert client._api_key == "default-block-key"

    def test_127_0_0_1_with_api_key_from_top_level(self, tmp_path):
        """When baseUrl is 127.0.0.1 and api_key comes from top-level, use it."""
        config_file = tmp_path / "honcho.json"
        config_file.write_text(
            json.dumps({
                "baseUrl": "http://127.0.0.1:8000",
                "apiKey": "top-level-key",
                "hosts": {
                    "hermes": {},
                    "hermes_profile5": {},
                }
            })
        )

        with patch("plugins.memory.honcho.client.resolve_config_path", return_value=config_file):
            with patch("plugins.memory.honcho.client.resolve_active_host", return_value="hermes_profile5"):
                config = HonchoClientConfig.from_global_config(host="hermes_profile5")
                assert config.api_key == "top-level-key"

                from plugins.memory.honcho.client import get_honcho_client
                client = get_honcho_client(config)
                assert client._api_key == "top-level-key"

    def test_localhost_without_api_key_uses_local_placeholder(self, tmp_path):
        """When truly no apiKey configured, still use 'local' placeholder for localhost."""
        config_file = tmp_path / "honcho.json"
        config_file.write_text(
            json.dumps({
                "baseUrl": "http://localhost:8000",
                "hosts": {
                    "hermes": {},
                    "hermes_profile6": {},
                }
            })
        )

        with patch("plugins.memory.honcho.client.resolve_config_path", return_value=config_file):
            with patch("plugins.memory.honcho.client.resolve_active_host", return_value="hermes_profile6"):
                config = HonchoClientConfig.from_global_config(host="hermes_profile6")
                assert config.api_key is None

                from plugins.memory.honcho.client import get_honcho_client
                client = get_honcho_client(config)
                # When api_key is None and _is_local, should use "local" placeholder
                assert client._api_key == "local"

    def test_non_localhost_always_uses_config_api_key(self, tmp_path):
        """For non-localhost URLs, always use config.api_key regardless of host block."""
        config_file = tmp_path / "honcho.json"
        config_file.write_text(
            json.dumps({
                "baseUrl": "https://api.honcho.app",
                "apiKey": "top-level-key",
                "hosts": {
                    "hermes": {},
                    "hermes_profile7": {},
                }
            })
        )

        with patch("plugins.memory.honcho.client.resolve_config_path", return_value=config_file):
            with patch("plugins.memory.honcho.client.resolve_active_host", return_value="hermes_profile7"):
                config = HonchoClientConfig.from_global_config(host="hermes_profile7")
                assert config.api_key == "top-level-key"

                from plugins.memory.honcho.client import get_honcho_client
                client = get_honcho_client(config)
                assert client._api_key == "top-level-key"


class TestIssue36098Timeout:
    """Test that default timeout is 60s."""

    def test_default_timeout_is_60_seconds(self):
        """Issue #36098: Default HTTP timeout bumped from 30s to 60s."""
        assert _DEFAULT_HTTP_TIMEOUT == 60.0


class TestIssue36098ErrorMarker:
    """Test that dialectic_query returns non-empty error marker on failure."""

    def test_dialectic_query_returns_error_marker_on_timeout(self):
        """On timeout exception, dialectic_query should return '[honcho_error: Timeout]'. """
        from plugins.memory.honcho.session import HonchoSession
        from unittest.mock import patch, MagicMock

        # Create a mock Honcho instance
        mock_honcho = MagicMock()
        mock_workspace = MagicMock()
        mock_workspace.peers = MagicMock()
        mock_user_peer = MagicMock()
        mock_user_peer.chat = MagicMock(side_effect=Exception("Connection timeout"))
        mock_workspace.peers.get_or_create = MagicMock(return_value=mock_user_peer)
        mock_honcho.workspaces = MagicMock()
        mock_honcho.workspaces.get = MagicMock(return_value=mock_workspace)

        session = HonchoSession(honcho=mock_honcho, config=MagicMock())
        session._cache = {"test-session": MagicMock(assistant_peer_id="peer-123")}
        session._resolve_peer_id = MagicMock(return_value="user-peer")
        session._dialectic_max_input_chars = 10000
        session._dialectic_dynamic = False
        session._default_reasoning_level = MagicMock(return_value="low")
        session._dialectic_max_chars = 600
        session._ai_observe_others = True

        result = session.dialectic_query("test-session", "test query")

        # Should return non-empty error marker
        assert result != ""
        assert result.startswith("[honcho_error:")
        assert "Exception" in result

    def test_dialectic_query_error_marker_distinguishes_from_no_data(self):
        """Error marker should be distinguishable from empty result (no relevant data)."""
        from plugins.memory.honcho.session import HonchoSession
        from unittest.mock import MagicMock

        # Create a mock Honcho instance
        mock_honcho = MagicMock()
        mock_workspace = MagicMock()
        mock_workspace.peers = MagicMock()
        mock_user_peer = MagicMock()
        mock_user_peer.chat = MagicMock(return_value="")  # No relevant data
        mock_workspace.peers.get_or_create = MagicMock(return_value=mock_user_peer)
        mock_honcho.workspaces = MagicMock()
        mock_honcho.workspaces.get = MagicMock(return_value=mock_workspace)

        session = HonchoSession(honcho=mock_honcho, config=MagicMock())
        session._cache = {"test-session": MagicMock(assistant_peer_id="peer-123")}
        session._resolve_peer_id = MagicMock(return_value="user-peer")
        session._dialectic_max_input_chars = 10000
        session._dialectic_dynamic = False
        session._default_reasoning_level = MagicMock(return_value="low")
        session._dialectic_max_chars = 600
        session._ai_observe_others = True

        result = session.dialectic_query("test-session", "test query")

        # Empty result is legitimate "no relevant data"
        assert result == ""

        # Now test with exception
        mock_user_peer.chat = MagicMock(side_effect=Exception("Some error"))
        result = session.dialectic_query("test-session", "test query")

        # Error result is non-empty and contains marker
        assert result != ""
        assert result.startswith("[honcho_error:")