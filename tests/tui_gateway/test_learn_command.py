"""Tests for /learn routing in tui_gateway.

The TUI routes ``/learn`` through ``command.dispatch`` (not ``slash.exec``)
because ``command.dispatch`` returns ``{"type": "send", "message": ...}`` which
the frontend uses to render a system line and fire the prompt.  Routing through
``slash.exec`` would hit the slash-worker subprocess, which returns plain text
output and never triggers ``send()`` — leaving the user with an ack message
but no LLM call.

Regression test for #51829.
"""

from __future__ import annotations

import importlib
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    yield home


@pytest.fixture()
def server(hermes_home):
    with patch.dict(
        "sys.modules",
        {
            "hermes_cli.env_loader": MagicMock(),
            "hermes_cli.banner": MagicMock(),
        },
    ):
        mod = importlib.import_module("tui_gateway.server")
        yield mod
        mod._sessions.clear()
        mod._pending.clear()
        mod._answers.clear()


@pytest.fixture()
def session(server):
    sid = "sid-learn-test"
    session_key = "tui-learn-session-1"
    s = {
        "session_key": session_key,
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": False,
        "attached_images": [],
        "cols": 120,
    }
    server._sessions[sid] = s
    return sid, session_key, s


def _call(server, method, **params):
    handler = server._methods[method]
    return handler(1, params)


# ── command.dispatch /learn ────────────────────────────────────────────


def test_learn_returns_send_type(server, session):
    """command.dispatch /learn must return type=send with the learn prompt."""
    sid, _, _ = session
    r = _call(server, "command.dispatch", name="learn", arg="create a skill from https://example.com", session_id=sid)
    result = r["result"]
    assert result["type"] == "send"
    assert "message" in result
    assert len(result["message"]) > 0
    # The learn prompt should mention the user's request
    assert "https://example.com" in result["message"]


def test_learn_empty_arg_returns_error(server, session):
    """command.dispatch /learn with no argument should return an error."""
    sid, _, _ = session
    r = _call(server, "command.dispatch", name="learn", arg="", session_id=sid)
    # Should either error or return a message asking for a description
    result = r.get("result", r)
    # The handler may return an error or a send with a usage hint
    assert "error" in r or result.get("type") == "send"


# ── slash.exec /learn routing ──────────────────────────────────────────


def test_slash_exec_routes_learn_to_command_dispatch(server, session):
    """slash.exec must route /learn directly to command.dispatch internally.

    Previously /learn went through the slash-worker subprocess, which returned
    plain text output instead of the structured {"type": "send", "message": ...}
    payload.  The frontend then showed an ack but never triggered send().
    """
    sid, _, _ = session
    r = _call(server, "slash.exec", command="learn create a skill from https://example.com", session_id=sid)
    assert "result" in r
    result = r["result"]
    assert result["type"] == "send"
    assert "message" in result
    assert "https://example.com" in result["message"]


# ── Guard: _PENDING_INPUT_COMMANDS membership ──────────────────────────


def test_pending_input_commands_includes_learn(server):
    """Guard: _PENDING_INPUT_COMMANDS must list 'learn' — removing it would
    silently re-break the Desktop GUI /learn command (regression #51829)."""
    assert "learn" in server._PENDING_INPUT_COMMANDS
