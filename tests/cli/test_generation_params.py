"""Tests for model.generation_params config option.

Verifies that user-configured sampling parameters (temperature, top_p, top_k)
are forwarded as top-level API request parameters via request_overrides.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestCLIGenerationParams:
    """CLI _resolve_turn_agent_config reads generation_params from config."""

    def _make_shell(self, **attrs):
        """Create a minimal mock shell with required attributes."""
        shell = MagicMock()
        shell.model = "test-model"
        shell.api_key = "test-key"
        shell.base_url = "http://localhost:8080/v1"
        shell.provider = "custom"
        shell.api_mode = "chat_completions"
        shell.acp_command = None
        shell.acp_args = []
        shell._credential_pool = None
        shell.service_tier = None
        for k, v in attrs.items():
            setattr(shell, k, v)
        return shell

    def test_generation_params_merged_into_request_overrides(self):
        """generation_params from config should appear in request_overrides."""
        from hermes_cli.cli_agent_setup_mixin import CLIAgentSetupMixin

        shell = self._make_shell()
        config = {
            "model": {
                "default": "my-local-model",
                "generation_params": {
                    "temperature": 1.0,
                    "top_p": 0.95,
                    "top_k": 64,
                },
            }
        }

        with patch("hermes_cli.cli_agent_setup_mixin.CLI_CONFIG", config, create=True):
            # Bind the method to our mock shell
            method = CLIAgentSetupMixin._resolve_turn_agent_config.__get__(shell)
            with patch("cli.CLI_CONFIG", config):
                route = method("hello")

        overrides = route["request_overrides"]
        assert overrides is not None
        assert overrides["temperature"] == 1.0
        assert overrides["top_p"] == 0.95
        assert overrides["top_k"] == 64

    def test_no_generation_params_returns_none(self):
        """Without generation_params, request_overrides should be None."""
        from hermes_cli.cli_agent_setup_mixin import CLIAgentSetupMixin

        shell = self._make_shell()
        config = {"model": "test-model"}

        method = CLIAgentSetupMixin._resolve_turn_agent_config.__get__(shell)
        with patch("cli.CLI_CONFIG", config):
            route = method("hello")

        assert route["request_overrides"] is None

    def test_generation_params_with_fast_mode(self):
        """generation_params should merge with fast-mode overrides."""
        from hermes_cli.cli_agent_setup_mixin import CLIAgentSetupMixin

        shell = self._make_shell(service_tier="priority")
        config = {
            "model": {
                "default": "test-model",
                "generation_params": {"temperature": 0.7},
            }
        }

        method = CLIAgentSetupMixin._resolve_turn_agent_config.__get__(shell)
        with patch("cli.CLI_CONFIG", config):
            with patch(
                "hermes_cli.models.resolve_fast_mode_overrides",
                return_value={"service_tier": "priority"},
            ):
                route = method("hello")

        overrides = route["request_overrides"]
        assert overrides is not None
        assert overrides["temperature"] == 0.7
        assert overrides.get("service_tier") == "priority"

    def test_generation_params_string_model_no_crash(self):
        """When model is a string (not dict), should not crash."""
        from hermes_cli.cli_agent_setup_mixin import CLIAgentSetupMixin

        shell = self._make_shell()
        config = {"model": "openrouter/claude-sonnet-4"}

        method = CLIAgentSetupMixin._resolve_turn_agent_config.__get__(shell)
        with patch("cli.CLI_CONFIG", config):
            route = method("hello")

        assert route["request_overrides"] is None

    def test_generation_params_empty_dict_ignored(self):
        """Empty generation_params dict should not create overrides."""
        from hermes_cli.cli_agent_setup_mixin import CLIAgentSetupMixin

        shell = self._make_shell()
        config = {
            "model": {
                "default": "test-model",
                "generation_params": {},
            }
        }

        method = CLIAgentSetupMixin._resolve_turn_agent_config.__get__(shell)
        with patch("cli.CLI_CONFIG", config):
            route = method("hello")

        assert route["request_overrides"] is None

    def test_generation_params_non_dict_ignored(self):
        """Non-dict generation_params should be silently ignored."""
        from hermes_cli.cli_agent_setup_mixin import CLIAgentSetupMixin

        shell = self._make_shell()
        config = {
            "model": {
                "default": "test-model",
                "generation_params": "invalid",
            }
        }

        method = CLIAgentSetupMixin._resolve_turn_agent_config.__get__(shell)
        with patch("cli.CLI_CONFIG", config):
            route = method("hello")

        assert route["request_overrides"] is None


class TestGatewayGenerationParams:
    """Gateway _resolve_turn_agent_config reads generation_params from config."""

    def test_generation_params_merged_into_request_overrides(self):
        """generation_params from config should appear in gateway request_overrides."""
        from gateway.run import GatewayRunner

        runner = MagicMock(spec=GatewayRunner)
        runner._service_tier = None

        config = {
            "model": {
                "default": "my-local-model",
                "generation_params": {
                    "temperature": 1.0,
                    "top_p": 0.95,
                },
            }
        }

        method = GatewayRunner._resolve_turn_agent_config.__get__(runner)
        with patch("hermes_cli.config.load_config", return_value=config):
            route = method("hello", "my-local-model", {
                "api_key": "k",
                "base_url": "http://localhost:8080/v1",
                "provider": "custom",
                "api_mode": "chat_completions",
            })

        overrides = route["request_overrides"]
        assert overrides["temperature"] == 1.0
        assert overrides["top_p"] == 0.95

    def test_no_generation_params_returns_empty_dict(self):
        """Without generation_params, gateway returns empty dict (not None)."""
        from gateway.run import GatewayRunner

        runner = MagicMock(spec=GatewayRunner)
        runner._service_tier = None

        config = {"model": "test-model"}

        method = GatewayRunner._resolve_turn_agent_config.__get__(runner)
        with patch("hermes_cli.config.load_config", return_value=config):
            route = method("hello", "test-model", {
                "api_key": "k",
                "base_url": "http://localhost:8080/v1",
                "provider": "custom",
                "api_mode": "chat_completions",
            })

        assert route["request_overrides"] == {}


class TestDumpGenerationParams:
    """hermes config display shows generation_params when configured."""

    def test_generation_params_in_dump_output(self):
        """generation_params should appear in dump output."""
        from hermes_cli.dump import _config_overrides

        config = {
            "model": {
                "default": "test-model",
                "generation_params": {
                    "temperature": 1.0,
                    "top_p": 0.95,
                },
            }
        }

        with patch("hermes_cli.dump.load_config", return_value=config):
            overrides = _config_overrides(config)

        assert "model.generation_params" in overrides
        assert "1.0" in overrides["model.generation_params"]

    def test_empty_generation_params_not_in_dump(self):
        """Empty generation_params should not appear in dump."""
        from hermes_cli.dump import _config_overrides

        config = {
            "model": {
                "default": "test-model",
                "generation_params": {},
            }
        }

        with patch("hermes_cli.dump.load_config", return_value=config):
            overrides = _config_overrides(config)

        assert "model.generation_params" not in overrides


class TestTransportIntegration:
    """Verify generation_params flow through to the transport layer."""

    def test_request_overrides_applied_to_api_kwargs(self):
        """request_overrides with temperature/top_p should set api_kwargs."""
        from agent.transports.chat_completions import ChatCompletionsTransport

        transport = ChatCompletionsTransport()

        # Mock the profile to return no extras
        mock_profile = MagicMock()
        mock_profile.fixed_temperature = None
        mock_profile.get_max_tokens.return_value = None
        mock_profile.build_api_kwargs_extras.return_value = ({}, {})
        mock_profile.build_extra_body.return_value = {}

        params = {
            "request_overrides": {
                "temperature": 1.0,
                "top_p": 0.95,
                "top_k": 64,
            },
        }

        result = transport.build_kwargs(
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            base_url="http://localhost:8080/v1",
            timeout=30,
            max_tokens=4096,
            is_openrouter=False,
            is_nous=False,
            is_qwen_portal=False,
            is_github_models=False,
            is_nvidia_nim=False,
            is_kimi=False,
            is_tokenhub=False,
            is_lmstudio=False,
            is_custom_provider=True,
            profile=mock_profile,
            **{k: v for k, v in params.items()},
        )

        assert result["temperature"] == 1.0
        assert result["top_p"] == 0.95
        assert result["top_k"] == 64
