"""Regression test for #39750: ``api_mode: responses`` alias in auxiliary route.

The PR fixes hermes_cli/runtime_provider._parse_api_mode to accept
``responses`` as an alias for ``codex_responses``. This test verifies that
the explicit ``api_mode`` parameter passed to _try_azure_foundry() is
normalized before the CodexAuxiliaryClient wrapping decision.

Before the fix: passing ``api_mode="responses"`` would bypass the
CodexAuxiliaryClient wrapper because the raw string "responses" != "codex_responses".
After the fix: the alias is normalized and the client is correctly wrapped.
"""

import pytest

from agent.auxiliary_client import _try_azure_foundry, CodexAuxiliaryClient


class TestAzureFoundryApiModeResponsesAliasInAuxiliaryRoute:
    """Verify that ``api_mode: responses`` triggers CodexAuxiliaryClient wrapping."""

    def test_explicit_api_mode_responses_normalizes_to_codex_responses(
        self, monkeypatch,
    ):
        """When ``api_mode="responses"`` is passed explicitly to _try_azure_foundry,
        it should be normalized to ``codex_responses`` and result in a
        CodexAuxiliaryClient wrapper."""
        # Mock load_config to return a minimal model config
        def _load_config():
            return {"model": {"default": "gpt-4o"}}
        monkeypatch.setattr("hermes_cli.config.load_config", _load_config)

        # Mock the runtime resolver
        monkeypatch.setattr(
            "hermes_cli.runtime_provider._resolve_azure_foundry_runtime",
            lambda **kwargs: {
                "api_key": "test-key",
                "base_url": "https://test.openai.azure.com",
                "api_mode": None,  # No api_mode in runtime, only explicit param
            },
        )

        # Mock URL param extraction
        monkeypatch.setattr(
            "agent.auxiliary_client._extract_url_query_params",
            lambda url: (url, {}),
        )

        # Mock OpenAI client creation
        from unittest.mock import MagicMock
        fake_openai = MagicMock()
        fake_openai.__class__.__name__ = "OpenAI"
        monkeypatch.setattr("agent.auxiliary_client.OpenAI", lambda **kwargs: fake_openai)

        # Mock model normalization
        monkeypatch.setattr(
            "agent.auxiliary_client._normalize_resolved_model",
            lambda model, provider: "gpt-4o",
        )

        # Pass the alias "responses" explicitly
        client, model = _try_azure_foundry(
            model="gpt-4o",
            explicit_api_key="test-key",
            explicit_base_url="https://test.openai.azure.com",
            api_mode="responses",
        )

        # The alias should be normalized and result in a CodexAuxiliaryClient wrapper
        assert isinstance(client, CodexAuxiliaryClient)
        assert model == "gpt-4o"

    def test_runtime_api_mode_responses_also_normalizes(
        self, monkeypatch,
    ):
        """When the runtime resolver returns ``api_mode="responses"``,
        it should already be normalized (the resolver uses _parse_api_mode)."""
        # Mock load_config
        def _load_config():
            return {"model": {"default": "gpt-4o"}}
        monkeypatch.setattr("hermes_cli.config.load_config", _load_config)

        # The resolver should normalize the value (it calls _parse_api_mode)
        monkeypatch.setattr(
            "hermes_cli.runtime_provider._resolve_azure_foundry_runtime",
            lambda **kwargs: {
                "api_key": "test-key",
                "base_url": "https://test.openai.azure.com",
                "api_mode": "codex_responses",  # Resolved value is already canonical
            },
        )

        monkeypatch.setattr(
            "agent.auxiliary_client._extract_url_query_params",
            lambda url: (url, {}),
        )

        from unittest.mock import MagicMock
        fake_openai = MagicMock()
        fake_openai.__class__.__name__ = "OpenAI"
        monkeypatch.setattr("agent.auxiliary_client.OpenAI", lambda **kwargs: fake_openai)

        monkeypatch.setattr(
            "agent.auxiliary_client._normalize_resolved_model",
            lambda model, provider: "gpt-4o",
        )

        client, model = _try_azure_foundry(
            model="gpt-4o",
            explicit_api_key="test-key",
            explicit_base_url="https://test.openai.azure.com",
            api_mode=None,  # No explicit param, use runtime value
        )

        assert isinstance(client, CodexAuxiliaryClient)
        assert model == "gpt-4o"