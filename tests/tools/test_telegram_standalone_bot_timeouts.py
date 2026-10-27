"""The standalone Telegram send lane must honour ``HERMES_TELEGRAM_HTTP_*`` timeouts.

Issue #145412: ``_telegram_bot`` built its ``Bot`` without timeouts, so the fallback route (used
when the live gateway adapter can't deliver) ran on python-telegram-bot's 5s/1s defaults while the
live adapter honoured ``HERMES_TELEGRAM_HTTP_CONNECT/READ/WRITE/POOL_TIMEOUT`` (defaults
10/20/20/8) — an operator raising those budgets got no effect on the degraded lane that needs
them most. The proxy log also leaked embedded ``user:password`` credentials into agent.log.

These tests confirm, per ``Bot`` construction:
  1. the no-proxy branch gets the adapter's default timeouts (and env overrides),
  2. the proxy branch passes the proxy together with the same timeouts,
  3. the proxy log line carries the host but not the credentials,
  4. a proxy-resolution failure still falls back to a direct bot with timeouts.
"""
import logging

import pytest

import telegram
import telegram.request
from tools.send_message_senders import _telegram_bot

_TIMEOUT_ENV_VARS = {
    "HERMES_TELEGRAM_HTTP_CONNECT_TIMEOUT": 11.0,
    "HERMES_TELEGRAM_HTTP_READ_TIMEOUT": 21.0,
    "HERMES_TELEGRAM_HTTP_WRITE_TIMEOUT": 22.0,
    "HERMES_TELEGRAM_HTTP_POOL_TIMEOUT": 9.0,
}
_DEFAULT_TIMEOUTS = {"connect_timeout": 10.0, "read_timeout": 20.0, "write_timeout": 20.0, "pool_timeout": 8.0}


class _FakeRequest:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeRequest.instances.append(self)


class _FakeBot:
    def __init__(self, *, token, request=None, get_updates_request=None):
        self.token = token
        self.request = request
        self.get_updates_request = get_updates_request


@pytest.fixture(autouse=True)
def _fake_ptb(monkeypatch):
    _FakeRequest.instances = []
    monkeypatch.setattr(telegram, "Bot", _FakeBot)
    monkeypatch.setattr(telegram.request, "HTTPXRequest", _FakeRequest)


@pytest.fixture(autouse=True)
def _clean_timeout_env(monkeypatch):
    for var in _TIMEOUT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _resolve_proxy(monkeypatch, value):
    import gateway.platforms.base

    def _fake_resolve(platform_env_var, **_kwargs):
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(gateway.platforms.base, "resolve_proxy_url", _fake_resolve)


def test_no_proxy_branch_gets_adapter_default_timeouts(monkeypatch):
    _resolve_proxy(monkeypatch, None)
    bot = _telegram_bot("t0k3n")
    assert bot.token == "t0k3n"
    assert len(_FakeRequest.instances) == 2
    assert bot.request.kwargs == _DEFAULT_TIMEOUTS
    assert bot.get_updates_request.kwargs == _DEFAULT_TIMEOUTS


def test_no_proxy_branch_applies_env_overrides(monkeypatch):
    for var, val in _TIMEOUT_ENV_VARS.items():
        monkeypatch.setenv(var, str(val))
    _resolve_proxy(monkeypatch, None)
    bot = _telegram_bot("t0k3n")
    assert bot.request.kwargs == {"connect_timeout": 11.0, "read_timeout": 21.0,
                                  "write_timeout": 22.0, "pool_timeout": 9.0}


def test_proxy_branch_sends_proxy_with_timeouts(monkeypatch):
    _resolve_proxy(monkeypatch, "http://proxy.internal:7890")
    bot = _telegram_bot("t0k3n")
    expected = {**_DEFAULT_TIMEOUTS, "proxy": "http://proxy.internal:7890"}
    assert bot.request.kwargs == expected
    assert bot.get_updates_request.kwargs == expected


def test_proxy_log_redacts_userinfo(monkeypatch, caplog):
    _resolve_proxy(monkeypatch, "http://alice:s3cr3t@127.0.0.1:7890")
    with caplog.at_level(logging.INFO, logger="tools.send_message_tool"):
        _telegram_bot("t0k3n")
    line = " ".join(r.getMessage() for r in caplog.records)
    assert "alice" not in line and "s3cr3t" not in line
    assert "127.0.0.1:7890" in line


def test_proxy_resolution_failure_falls_back_direct_with_timeouts(monkeypatch, caplog):
    _resolve_proxy(monkeypatch, RuntimeError("resolver exploded"))
    with caplog.at_level(logging.WARNING, logger="tools.send_message_tool"):
        bot = _telegram_bot("t0k3n")
    assert "resolver exploded" in " ".join(r.getMessage() for r in caplog.records)
    assert len(_FakeRequest.instances) == 2
    assert bot.request.kwargs == _DEFAULT_TIMEOUTS
    assert bot.get_updates_request.kwargs == _DEFAULT_TIMEOUTS
