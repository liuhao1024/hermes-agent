"""Tests for the MCPServerTask reconnect signal.

When the OAuth layer cannot recover in-place (e.g., external refresh of a
single-use refresh_token made the SDK's in-memory refresh fail), the tool
handler signals MCPServerTask to tear down the current MCP session and
reconnect with fresh credentials. This file exercises the signal plumbing
in isolation from the full stdio/http transport machinery.
"""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_reconnect_event_attribute_exists():
    """MCPServerTask has a _reconnect_event alongside _shutdown_event."""
    from tools.mcp_tool import MCPServerTask
    task = MCPServerTask("test")
    assert hasattr(task, "_reconnect_event")
    assert isinstance(task._reconnect_event, asyncio.Event)
    assert not task._reconnect_event.is_set()


@pytest.mark.asyncio
async def test_wait_for_lifecycle_event_returns_reconnect():
    """When _reconnect_event fires, helper returns 'reconnect' and clears it."""
    from tools.mcp_tool import MCPServerTask
    task = MCPServerTask("test")

    task._reconnect_event.set()
    reason = await task._wait_for_lifecycle_event()
    assert reason == "reconnect"
    # Should have cleared so the next cycle starts fresh
    assert not task._reconnect_event.is_set()


@pytest.mark.asyncio
async def test_wait_for_lifecycle_event_returns_shutdown():
    """When _shutdown_event fires, helper returns 'shutdown'."""
    from tools.mcp_tool import MCPServerTask
    task = MCPServerTask("test")

    task._shutdown_event.set()
    reason = await task._wait_for_lifecycle_event()
    assert reason == "shutdown"


@pytest.mark.asyncio
async def test_wait_for_lifecycle_event_shutdown_wins_when_both_set():
    """If both events are set simultaneously, shutdown takes precedence."""
    from tools.mcp_tool import MCPServerTask
    task = MCPServerTask("test")

    task._shutdown_event.set()
    task._reconnect_event.set()
    reason = await task._wait_for_lifecycle_event()
    assert reason == "shutdown"


@pytest.mark.asyncio
async def test_auth_error_during_reconnect_parks_immediately(monkeypatch):
    """When an auth error (e.g., OAuthNonInteractiveError) fires during a
    reconnect attempt, the server should park immediately instead of burning
    through all 5 retries.  Each retry would re-enter the full OAuth flow
    (300s callback timeout) before failing again.  See #56673."""
    from tools.mcp_tool import MCPServerTask, _server_error_counts
    from tools.mcp_oauth import OAuthNonInteractiveError

    task = MCPServerTask("test-srv")

    # Simulate: server was previously connected (_ready set), then a
    # reconnect triggered an auth error.
    task._ready.set()

    call_count = 0

    async def _fake_run_http(self, config):
        nonlocal call_count
        call_count += 1
        raise OAuthNonInteractiveError(
            "OAuth callback timed out — no authorization code received."
        )

    # Park → immediately request shutdown so run() exits
    async def _fake_park(self):
        task._shutdown_event.set()
        return "shutdown"

    monkeypatch.setattr(type(task), "_run_http", _fake_run_http)
    monkeypatch.setattr(
        type(task), "_wait_for_reconnect_or_shutdown", _fake_park
    )

    # Clear any prior circuit-breaker state
    _server_error_counts.pop("test-srv", None)

    # Run — should exit after 1 failed attempt + park, not 5 retries.
    # Pass url so _is_http() returns True.
    await task.run({"url": "http://fake", "auth": "oauth"})

    # Only 1 attempt before parking (not 5 retries + backoff)
    assert call_count == 1, (
        f"expected 1 attempt before parking, got {call_count}"
    )
    # Circuit breaker should have been bumped
    assert _server_error_counts.get("test-srv", 0) >= 1
