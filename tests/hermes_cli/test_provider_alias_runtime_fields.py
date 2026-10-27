"""A documented provider alias in ``model.provider`` must not void the config's own fields.

Regression coverage for the alias-shaped config drop: ``_same_registered_provider``
compared registry keys only, so a profile written with a documented alias —
``model.provider: github-copilot`` (plugin alias of ``copilot``), ``moonshot``
(alias of ``kimi-coding``) — was judged a *different* provider at runtime even
though ``auth.resolve_provider`` normalizes the very same string to the canonical
id everywhere else. Both persisted fields were then silently dropped:

* ``model.api_mode`` (``_configured_api_mode`` -> None): a Responses-only Copilot
  GPT-5+ model was sent to ``/chat/completions`` -> HTTP 400 on every turn.
* ``model.base_url`` (``_config_base_url_for_provider`` -> "").

See #145475.
"""

from __future__ import annotations

import pytest

from hermes_cli.runtime_provider import (
    _config_base_url_for_provider,
    _configured_api_mode,
    _provider_supports_explicit_api_mode,
    _same_registered_provider,
)


def _alias_registered(alias: str) -> bool:
    from hermes_cli import auth as auth_mod

    return alias in auth_mod._plugin_aliases()


class TestAliasCountsAsSameRegisteredProvider:
    """``moonshot`` is a hardcoded alias of ``kimi-coding`` — one provider, one registry ID."""

    def test_alias_resolves_to_canonical(self):
        assert _same_registered_provider("kimi-coding", "moonshot") is True

    def test_alias_in_either_argument(self):
        assert _same_registered_provider("moonshot", "kimi-coding") is True

    def test_explicit_api_mode_allowed_across_alias(self):
        assert _provider_supports_explicit_api_mode("kimi-coding", "moonshot") is True


class TestAliasKeepsPersistedFields:
    """The two consumption sites that silently dropped the alias-shaped config."""

    def test_api_mode_honored_for_alias_provider(self):
        cfg = {"provider": "moonshot", "api_mode": "codex_responses"}
        assert _configured_api_mode("kimi-coding", cfg) == "codex_responses"

    def test_base_url_honored_for_alias_provider(self):
        cfg = {"provider": "moonshot", "base_url": "https://relay.example.invalid/v1"}
        assert _config_base_url_for_provider(cfg, "kimi-coding") == "https://relay.example.invalid/v1"


class TestUnrelatedRoutesStillDistinct:
    """The stale-field guard must keep firing for genuinely different providers."""

    def test_unrelated_providers_stay_distinct(self):
        assert _same_registered_provider("kimi-coding", "openai") is False

    def test_api_mode_still_dropped_across_providers(self):
        cfg = {"provider": "openai", "api_mode": "codex_responses"}
        assert _configured_api_mode("kimi-coding", cfg) is None

    def test_base_url_still_dropped_across_providers(self):
        cfg = {"provider": "openai", "base_url": "https://relay.example.invalid/v1"}
        assert _config_base_url_for_provider(cfg, "kimi-coding") == ""

    def test_unknown_strings_stay_distinct(self):
        assert _same_registered_provider("not-a-provider", "also-not-one") is False


class TestCopilotAliasScenario:
    """The exact #145475 shape — only meaningful where the copilot plugin alias exists."""

    @pytest.mark.skipif(
        not _alias_registered("github-copilot"),
        reason="copilot plugin not registered in this environment",
    )
    def test_github_copilot_api_mode_and_base_url_kept(self):
        assert _same_registered_provider("copilot", "github-copilot") is True
        cfg = {
            "provider": "github-copilot",
            "api_mode": "codex_responses",
            "base_url": "https://api.business.githubcopilot.com",
        }
        assert _configured_api_mode("copilot", cfg) == "codex_responses"
        assert _config_base_url_for_provider(cfg, "copilot") == "https://api.business.githubcopilot.com"
