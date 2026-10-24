"""The "Anthropic Account" card must only read subscription credentials as Connected.

An API-key-only setup (``ANTHROPIC_API_KEY`` in ``~/.hermes/.env``) used to show the card
as Connected with "Sign in once in your terminal" helper text — presenting API-credit
billing as a subscription login, inconsistent with API-key providers like DeepSeek that
only appear under API keys (#144038).
"""

from fastapi.testclient import TestClient

from hermes_cli.web_server import _SESSION_TOKEN, app

client = TestClient(app)
HEADERS = {"X-Hermes-Session-Token": _SESSION_TOKEN}

_ANTHROPIC_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
# Synthetic placeholder values (not credentials), f-string assembled so secret scanners
# don't misread the fixtures as leaked keys.
_FAKE_API_KEY = f"sk-ant-api03-{'unit-test'}"
_FAKE_OAUTH_TOKEN = f"sk-ant-oat01-{'unit-test'}"


def _anthropic_card_status(monkeypatch, env: dict[str, str]) -> dict:
    from agent import anthropic_credentials

    monkeypatch.setattr(
        anthropic_credentials, "read_hermes_oauth_credentials", lambda: None
    )
    for var in _ANTHROPIC_VARS:
        monkeypatch.delenv(var, raising=False)
    # .env on the developer's machine must not leak a real credential into the assertion
    monkeypatch.setattr("hermes_cli.config.get_env_value", lambda _key: None)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    resp = client.get("/api/providers/oauth", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    providers = {p["id"]: p for p in resp.json()["providers"]}
    return providers["anthropic"]["status"]


def test_api_key_only_is_not_connected(monkeypatch):
    status = _anthropic_card_status(monkeypatch, {"ANTHROPIC_API_KEY": _FAKE_API_KEY})
    assert status["logged_in"] is False


def test_subscription_token_env_is_connected(monkeypatch):
    status = _anthropic_card_status(monkeypatch, {"ANTHROPIC_TOKEN": _FAKE_OAUTH_TOKEN})
    assert status["logged_in"] is True
    assert status["source"] == "env_var"
    assert "ANTHROPIC_TOKEN" in status["source_label"]


def test_claude_code_oauth_token_env_is_connected(monkeypatch):
    status = _anthropic_card_status(
        monkeypatch, {"CLAUDE_CODE_OAUTH_TOKEN": _FAKE_OAUTH_TOKEN}
    )
    assert status["logged_in"] is True
    assert "CLAUDE_CODE_OAUTH_TOKEN" in status["source_label"]


def test_api_key_does_not_connect_when_set_alongside_subscription(monkeypatch):
    """Both present: the subscription token wins the card, and dropping it (API key only)
    must fall back to not connected — the API key never masquerades as a login."""
    status = _anthropic_card_status(
        monkeypatch,
        {
            "ANTHROPIC_API_KEY": _FAKE_API_KEY,
            "CLAUDE_CODE_OAUTH_TOKEN": _FAKE_OAUTH_TOKEN,
        },
    )
    assert status["logged_in"] is True
    assert "CLAUDE_CODE_OAUTH_TOKEN" in status["source_label"]
