"""The ambient env priority must not override a pool-selected OAuth credential (#140998).

``resolve_anthropic_token`` ranks ``ANTHROPIC_API_KEY`` above discovered OAuth credentials —
the right semantics for an unmanaged resolve, but ``_try_refresh_anthropic_client_credentials``
runs before every native-Anthropic request. There it lets the env key silently re-adopt the
client on each attempt, undoing the pool's rotation to the OAuth entry: the session keeps
spending a key the pool already benched, and the retry loop never converges.

The invariant under test: a session the credential pool bound to an OAuth entry only accepts
an OAuth-shaped replacement (a rotated grant). Everything else — an env-key session adopting
an edited ``.env`` value, an unmanaged session honoring the env priority — is unchanged.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from run_agent import AIAgent

# Fake fixture values (not real credentials), assembled from the token shapes
# ``_is_oauth_token`` distinguishes: "sk-ant-" without the "-api" infix is OAuth-shaped,
# "sk-ant-api*" is a Console key.
_OAUTH_TOKEN = "sk-ant-" + "oauth-fixture-primary"
_ROTATED_OAUTH_TOKEN = "sk-ant-" + "oauth-fixture-rotated"
_ENV_API_KEY = "sk-ant-" + "api03-fixture-console"
_ENV_API_KEY_EDITED = "sk-ant-" + "api03-fixture-edited"
_CONSTRUCTOR_KEY = "test-key-" + "1234567890"


@pytest.fixture
def agent():
    """Minimal AIAgent construction, skipping tool discovery."""
    with (
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI"),
    ):
        a = AIAgent(
            api_key=_CONSTRUCTOR_KEY,
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        a.client = MagicMock()
        return a


def _native_anthropic_session(agent, *, oauth: bool) -> None:
    """Point the agent at a native Anthropic Messages session on the given credential kind."""
    agent.api_mode = "anthropic_messages"
    agent.provider = "anthropic"
    agent._anthropic_base_url = ""  # native default host
    agent._anthropic_client = MagicMock()
    agent._is_anthropic_oauth = oauth
    agent._anthropic_api_key = _OAUTH_TOKEN if oauth else _ENV_API_KEY


class TestPoolBoundOAuthEntry:
    """A pool-bound OAuth session must keep the pool's selection (#140998)."""

    def test_env_api_key_cannot_override_the_pool_rotation(self, agent):
        _native_anthropic_session(agent, oauth=True)
        agent._credential_pool = object()  # any truthy pool handle; only binding is decided here
        agent._credential_pool_entry_id = "anthropic-oauth-1"
        live_client = agent._anthropic_client

        with (
            patch("agent.anthropic_credentials.resolve_anthropic_token", return_value=_ENV_API_KEY),
            patch("agent.anthropic_adapter.build_anthropic_client") as build,
        ):
            assert agent._try_refresh_anthropic_client_credentials() is False

        assert agent._anthropic_api_key == _OAUTH_TOKEN
        assert agent._is_anthropic_oauth is True
        assert agent._anthropic_client is live_client  # not closed-and-rebuilt behind the env key
        build.assert_not_called()

    def test_rotated_oauth_grant_is_still_adopted(self, agent):
        _native_anthropic_session(agent, oauth=True)
        agent._credential_pool = object()
        agent._credential_pool_entry_id = "anthropic-oauth-1"

        with (
            patch("agent.anthropic_credentials.resolve_anthropic_token", return_value=_ROTATED_OAUTH_TOKEN),
            patch("agent.anthropic_adapter.build_anthropic_client", return_value=MagicMock()),
        ):
            assert agent._try_refresh_anthropic_client_credentials() is True

        assert agent._anthropic_api_key == _ROTATED_OAUTH_TOKEN
        assert agent._is_anthropic_oauth is True

    def test_unbound_pool_session_keeps_the_env_priority(self, agent):
        # Pool present but no entry bound (e.g. the entry was removed): ambient priority stands.
        _native_anthropic_session(agent, oauth=True)
        agent._credential_pool = object()
        agent._credential_pool_entry_id = None

        with (
            patch("agent.anthropic_credentials.resolve_anthropic_token", return_value=_ENV_API_KEY),
            patch("agent.anthropic_adapter.build_anthropic_client", return_value=MagicMock()),
        ):
            assert agent._try_refresh_anthropic_client_credentials() is True

        assert agent._anthropic_api_key == _ENV_API_KEY
        assert agent._is_anthropic_oauth is False


class TestEnvKeySessionUnaffected:
    """A session on the env-key entry keeps adopting ``.env`` edits (#67821)."""

    def test_edited_env_key_is_adopted_while_pool_bound(self, agent):
        _native_anthropic_session(agent, oauth=False)
        agent._credential_pool = object()
        agent._credential_pool_entry_id = "ANTHROPIC_API_KEY"

        with (
            patch("agent.anthropic_credentials.resolve_anthropic_token", return_value=_ENV_API_KEY_EDITED),
            patch("agent.anthropic_adapter.build_anthropic_client", return_value=MagicMock()),
        ):
            assert agent._try_refresh_anthropic_client_credentials() is True

        assert agent._anthropic_api_key == _ENV_API_KEY_EDITED
        assert agent._is_anthropic_oauth is False
