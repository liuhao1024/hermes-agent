"""Test that oneshot (-z) respects fallback_providers on AuthError (#60167)."""

from unittest.mock import patch

import pytest


class TestOneshotAuthFallback:
    """Oneshot must walk fallback_providers when primary fails at resolution time."""

    def test_auth_error_tries_fallback_provider(self, tmp_path, monkeypatch):
        """When primary provider raises AuthError, fallback is attempted."""
        from hermes_cli.auth import AuthError
        from hermes_cli.oneshot import _run_agent

        # Create a config with primary + fallback
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "model:\n  provider: openai-codex\n  default: codex-gpt-55-turbo\n"
            "fallback_providers:\n  - provider: openrouter\n    model: meta-llama/llama-4-maverick\n"
        )

        # Mock load_config to return our temp config
        def mock_load_config():
            import yaml
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}

        # load_config is imported locally inside _run_agent, so patch at the import site
        monkeypatch.setattr("hermes_cli.config.load_config", mock_load_config)

        call_count = {"n": 0}

        def _mock_resolve(**kwargs):
            call_count["n"] += 1
            # First call = primary (AuthError), second = fallback (success).
            if call_count["n"] == 1:
                raise AuthError("Codex token refresh failed with status 401")
            return {
                "api_key": "fallback-key",
                "base_url": "https://openrouter.ai/api/v1",
                "provider": "openrouter",
                "api_mode": "openai_chat",
                "command": None,
                "args": None,
                "credential_pool": None,
            }

        # Mock AIAgent.run_conversation to avoid actual agent execution
        def mock_run_conversation(self, prompt):
            return {
                "final_response": "PONG",
                "provider": self.provider,
                "model": self.model,
                "completed": True,
            }

        # Use patch at the correct import location; both resolve_runtime_provider and run_agent.AIAgent.run_conversation are locally imported in _run_agent
        with patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            side_effect=_mock_resolve,
        ), patch("run_agent.AIAgent.run_conversation", mock_run_conversation):
            response, result = _run_agent(
                prompt="Reply with one word: PONG",
                model=None,
                provider=None,
            )

        # Should have used the fallback provider/model
        assert response == "PONG"
        assert result.get("provider") == "openrouter"
        assert result.get("model") == "meta-llama/llama-4-maverick"
        # Should have called resolve_runtime_provider at least twice (primary + fallback)
        assert call_count["n"] >= 2

    def test_auth_error_no_fallback_raises(self, tmp_path, monkeypatch):
        """When primary fails and no fallback configured, original AuthError is raised."""
        from hermes_cli.auth import AuthError
        from hermes_cli.oneshot import _run_agent

        # Config without fallback
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "model:\n  provider: openai-codex\n  default: codex-gpt-55-turbo\n"
        )

        def mock_load_config():
            import yaml
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}

        monkeypatch.setattr("hermes_cli.config.load_config", mock_load_config)

        def _mock_resolve(**kwargs):
            raise AuthError("Codex token refresh failed with status 401")

        with patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            side_effect=_mock_resolve,
        ):
            with pytest.raises(AuthError, match="Codex token refresh failed"):
                _run_agent(prompt="test", model=None, provider=None)

    def test_all_fallbacks_fail_raises_original_error(self, tmp_path, monkeypatch):
        """When primary and all fallbacks fail, original AuthError is raised."""
        from hermes_cli.auth import AuthError
        from hermes_cli.oneshot import _run_agent

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "model:\n  provider: openai-codex\n  default: codex-gpt-55-turbo\n"
            "fallback_providers:\n  - provider: openrouter\n    model: meta-llama/llama-4-maverick\n"
        )

        def mock_load_config():
            import yaml
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}

        monkeypatch.setattr("hermes_cli.config.load_config", mock_load_config)

        def _mock_resolve(**kwargs):
            raise AuthError("All providers require fresh tokens")

        with patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            side_effect=_mock_resolve,
        ):
            with pytest.raises(AuthError, match="All providers require fresh tokens"):
                _run_agent(prompt="test", model=None, provider=None)

    def test_non_auth_error_propagates_immediately(self, tmp_path, monkeypatch):
        """Non-AuthError exceptions (e.g., config errors) propagate without fallback."""
        from hermes_cli.oneshot import _run_agent

        config_path = tmp_path / "config.yaml"
        config_path.write_text("model:\n  provider: openrouter\n")

        def mock_load_config():
            import yaml
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}

        monkeypatch.setattr("hermes_cli.config.load_config", mock_load_config)

        def _mock_resolve(**kwargs):
            raise ValueError("Invalid model ID")

        with patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            side_effect=_mock_resolve,
        ):
            with pytest.raises(ValueError, match="Invalid model ID"):
                _run_agent(prompt="test", model=None, provider=None)

    def test_primary_succeeds_no_fallback_attempt(self, tmp_path, monkeypatch):
        """When primary resolves successfully, fallback chain is never consulted."""
        from hermes_cli.oneshot import _run_agent

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "model:\n  provider: openrouter\n  default: meta-llama/llama-4-maverick\n"
            "fallback_providers:\n  - provider: openai-codex\n    model: codex-gpt-55-turbo\n"
        )

        def mock_load_config():
            import yaml
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}

        monkeypatch.setattr("hermes_cli.config.load_config", mock_load_config)

        call_count = {"n": 0}

        def _mock_resolve(**kwargs):
            call_count["n"] += 1
            return {
                "api_key": "primary-key",
                "base_url": "https://openrouter.ai/api/v1",
                "provider": "openrouter",
                "api_mode": "openai_chat",
                "command": None,
                "args": None,
                "credential_pool": None,
            }

        def mock_run_conversation(self, prompt):
            return {
                "final_response": "OK",
                "provider": self.provider,
                "model": self.model,
                "completed": True,
            }

        with patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            side_effect=_mock_resolve,
        ), patch("run_agent.AIAgent.run_conversation", mock_run_conversation):
            response, result = _run_agent(
                prompt="test", model=None, provider=None
            )

        # Should have called resolve_runtime_provider exactly once (primary only)
        assert call_count["n"] == 1
        assert result.get("provider") == "openrouter"