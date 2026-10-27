"""A transport teardown that hangs must not hold the server's run loop forever (#145292).

After a keepalive failure ``_serve_session`` returns ``"reconnect"`` and ``_serve_transport``
leaves its two context managers. Neither exit used to have a time bound: an HTTP session whose
close hangs kept the run loop waiting forever — no reconnect, no park, no self-probe, and the
deregistered tools never came back until a process restart. Both exits are now bounded; past the
window the teardown is abandoned with a warning and the body's result carries on.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from tools.mcp_tool import MCPServerTask


class _StalledExitCM:
    """Fake transport whose ``__aexit__`` never finishes, mimicking an HTTP session close stuck
    on a dead socket — the reported three-day stall."""

    async def __aenter__(self):
        return (object(), object())

    async def __aexit__(self, *exc_info):
        await asyncio.Event().wait()


class _QuietExitCM:
    """A transport that leaves normally: the common path, which must not gain a warning."""

    async def __aenter__(self):
        return (object(), object())

    async def __aexit__(self, *exc_info):
        return False


class _FakeSession:
    pass


class _FakeSessionCM:
    async def __aenter__(self):
        return _FakeSession()

    async def __aexit__(self, *exc_info):
        return False


@pytest.fixture
def task(monkeypatch):
    """A task whose served session returns at once, with a stand-in ClientSession CM and a
    teardown bound shrunk to a test-scale window."""
    server = MCPServerTask("teardown-bound")

    async def _instant_reconnect(session, connect_timeout, label="", mark_lifecycle=False):
        return "reconnect"

    monkeypatch.setattr(MCPServerTask, "_serve_session", _instant_reconnect)
    monkeypatch.setattr("tools.mcp_tool_transport._TEARDOWN_EXIT_TIMEOUT_S", 0.2, raising=False)
    monkeypatch.setattr(
        "tools.mcp_tool_transport._core",
        type("_StubCore", (), {
            "ClientSession": staticmethod(lambda *a, **k: _FakeSessionCM()),
            "_MCP_NOTIFICATION_TYPES": False,
            "_MCP_MESSAGE_HANDLER_SUPPORTED": False,
            "_MCP_LOGGING_CALLBACK_SUPPORTED": False,
            "_client_session_accepts": staticmethod(lambda name: False),
        }),
    )
    return server


@pytest.mark.asyncio
async def test_a_stalled_teardown_still_returns_reconnect(task, caplog):
    """The hang used to swallow the run loop whole: now the teardown is abandoned and the
    reconnect result goes on (this test times out before the fix)."""
    with caplog.at_level(logging.WARNING, logger="tools.mcp_tool"):
        reason = await asyncio.wait_for(
            task._serve_transport(_StalledExitCM(), "HTTP", 5.0), timeout=5.0)
    assert reason == "reconnect"
    assert any("teardown" in record.getMessage().lower() and "abandoned" in record.getMessage()
               for record in caplog.records), [r.getMessage() for r in caplog.records]


@pytest.mark.asyncio
async def test_a_normal_teardown_is_unchanged(task, caplog):
    """A transport that leaves promptly logs nothing and returns as before."""
    with caplog.at_level(logging.WARNING, logger="tools.mcp_tool"):
        reason = await task._serve_transport(_QuietExitCM(), "HTTP", 5.0)
    assert reason == "reconnect"
    assert not caplog.records, [r.getMessage() for r in caplog.records]
