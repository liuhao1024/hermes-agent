"""Regression tests for #142031 — a fallback swap that runs with no profile secret scope
under multiplexing drops a configured provider.

The swap's client construction resolves credentials through ``resolve_provider_client``;
scopeless under ``set_multiplex_active(True)`` the first env-var miss raises
``UnscopedSecretError`` (fail closed) and ``try_activate_fallback``'s generic handler
skipped the entry as if the provider were not configured. The swap now binds the launch
profile's runtime scope when it runs scopeless, and leaves an already-bound (any profile's
turn) scope untouched so a secondary's swap never borrows the launch profile's credentials.
"""

import contextlib
from unittest.mock import MagicMock, patch

import pytest

from agent import secret_scope
from run_agent import AIAgent

# Dummy fixture values assembled at runtime (not credentials of any real account).
_PRIMARY_DUMMY = "t" + "est-primary-key"
_GEMINI_DUMMY = "t" + "est-gemini-key-0123456789abcdef"


def _make_agent(fallback_model=None):
    with (
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI"),
    ):
        agent = AIAgent(
            api_key=_PRIMARY_DUMMY,
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            fallback_model=fallback_model,
        )
        agent.client = MagicMock()
        return agent


@pytest.fixture
def gemini_launch_home(tmp_path, monkeypatch):
    """A launch home whose .env carries only GEMINI_API_KEY (GOOGLE_API_KEY unset — the common
    Google AI Studio setup): the registry's first env var must miss before the second resolves,
    so an unscoped environ read is genuinely attempted."""
    home = tmp_path / "default"
    home.mkdir()
    (home / ".env").write_text(f"GEMINI_API_KEY={_GEMINI_DUMMY}\n")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


class TestUnscopedFallbackSwap:
    def test_scopeless_swap_binds_launch_profile_and_activates(
        self, gemini_launch_home
    ):
        """Under multiplexing with no scope installed, the swap binds the launch profile's
        scope and the configured fallback activates instead of being skipped."""
        agent = _make_agent(
            fallback_model=[{"provider": "gemini", "model": "gemini-3.8-flash"}]
        )
        secret_scope.set_multiplex_active(True)
        agent._rate_limited_until = 0
        assert agent._try_activate_fallback() is True
        assert agent.provider == "gemini"

    def test_bound_turn_scope_is_left_in_charge(self, gemini_launch_home):
        """With a profile scope already bound (a turn's), the guard must not enter the launch
        profile's scope: the swap resolves from the bound scope's own credentials."""
        import tui_gateway.launch_profile_policy as lpp

        entered = []
        agent = _make_agent(
            fallback_model=[{"provider": "gemini", "model": "gemini-3.8-flash"}]
        )
        secret_scope.set_multiplex_active(True)
        agent._rate_limited_until = 0
        token = secret_scope.set_secret_scope(
            {"GEMINI_API_KEY": "t" + "urn-scoped-key"}
        )
        try:
            with patch.object(
                lpp,
                "launch_profile_scope_if_multiplexed",
                side_effect=lambda: entered.append(True),
            ):
                assert agent._try_activate_fallback() is True
        finally:
            secret_scope.reset_secret_scope(token)
        assert entered == []

    def test_single_profile_process_never_binds_a_scope(self, gemini_launch_home):
        """Without multiplexing the guard is a no-op (single-profile installs are byte-identical)
        and the swap still resolves from the process env / .env as before."""
        from agent.route_binding import _fallback_route_scope

        assert isinstance(_fallback_route_scope(), contextlib.nullcontext)
        agent = _make_agent(
            fallback_model=[{"provider": "gemini", "model": "gemini-3.8-flash"}]
        )
        agent._rate_limited_until = 0
        assert agent._try_activate_fallback() is True
