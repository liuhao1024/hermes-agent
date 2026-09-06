"""Regression tests for #104622: ``resolve_anthropic_token()`` must consult the
Hermes-owned credential pool before borrowing the Claude Code login.

The ``claude_code`` pool row is a live mirror of ``~/.claude/.credentials.json``
(re-seeded on every ``load_pool()``), and the borrowed file itself refreshes and
rewrites that shared file on expiry — spending the single-use refresh token
Claude Code still holds, which logs every Claude Code process out. When a
Hermes-owned grant (``hermes auth add anthropic`` PKCE or a manual OAuth row)
exists, it must win; the borrowed login stays as the fallback for setups that
never registered one.
"""

import json
import time
from types import SimpleNamespace

import pytest

from agent.anthropic_credentials import resolve_anthropic_token

_CC_ACCESS = "sk-ant-oat01-cc-borrowed"
_CC_REFRESH = "sk-ant-ort01-cc-borrowed"
_POOL_ACCESS = "sk-ant-oat01-pool-owned"


def _clear_env(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)


def _claude_code_file(tmp_path, monkeypatch, *, expires_in_ms):
    cred_file = tmp_path / ".claude" / ".credentials.json"
    cred_file.parent.mkdir(parents=True)
    cred_file.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": _CC_ACCESS,
            "refreshToken": _CC_REFRESH,
            "expiresAt": int(time.time() * 1000) + expires_in_ms,
        }
    }))
    monkeypatch.setattr("agent.anthropic_credentials.Path.home", lambda: tmp_path)
    return cred_file


def _mock_pool(monkeypatch, entries):
    pool = SimpleNamespace(_available_entries=lambda **_kwargs: (list(entries), []))
    monkeypatch.setattr("agent.credential_pool.load_pool", lambda provider: pool)


def _owned_entry(source):
    return SimpleNamespace(
        auth_type="oauth", access_token=_POOL_ACCESS, refresh_token=None, source=source
    )


def _forbid_refresh(monkeypatch):
    def _fail(*_args, **_kwargs):
        raise AssertionError("the borrowed Claude Code login must not be refreshed when a pool grant exists")
    monkeypatch.setattr("agent.anthropic_credentials._refresh_oauth_token", _fail)


@pytest.mark.parametrize("source", ["hermes_pkce", "manual:hermes_pkce", "manual"])
def test_pool_grant_wins_over_valid_claude_code_login(tmp_path, monkeypatch, source):
    _clear_env(monkeypatch)
    _claude_code_file(tmp_path, monkeypatch, expires_in_ms=3_600_000)
    _mock_pool(monkeypatch, [_owned_entry(source)])
    _forbid_refresh(monkeypatch)

    assert resolve_anthropic_token() == _POOL_ACCESS


@pytest.mark.parametrize("source", ["hermes_pkce", "manual:hermes_pkce", "manual"])
def test_expired_claude_code_login_is_not_refreshed_when_pool_grant_exists(
    tmp_path, monkeypatch, source
):
    _clear_env(monkeypatch)
    _claude_code_file(tmp_path, monkeypatch, expires_in_ms=-3_600_000)
    _mock_pool(monkeypatch, [_owned_entry(source)])
    _forbid_refresh(monkeypatch)

    assert resolve_anthropic_token() == _POOL_ACCESS


def test_claude_code_login_still_used_when_pool_holds_only_the_borrowed_row(
    tmp_path, monkeypatch
):
    """Setups that never ran ``hermes auth add anthropic`` keep the fallback.

    The mirror row must not be leased from the pool side either: deferring to
    the file path keeps its refresh-on-expiry behaviour identical to today.
    """
    _clear_env(monkeypatch)
    _claude_code_file(tmp_path, monkeypatch, expires_in_ms=3_600_000)
    _mock_pool(monkeypatch, [SimpleNamespace(
        auth_type="oauth", access_token=_CC_ACCESS, refresh_token=_CC_REFRESH,
        source="claude_code",
    )])

    assert resolve_anthropic_token() == _CC_ACCESS


def test_api_key_still_wins_over_pool_and_borrowed_login(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-mykey")
    _claude_code_file(tmp_path, monkeypatch, expires_in_ms=3_600_000)
    _mock_pool(monkeypatch, [_owned_entry("hermes_pkce")])

    assert resolve_anthropic_token() == "sk-ant-api03-mykey"
