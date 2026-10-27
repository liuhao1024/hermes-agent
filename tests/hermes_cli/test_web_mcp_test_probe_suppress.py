"""Regression test: the dashboard's ``POST /api/mcp/servers/{name}/test`` probe must never
fall through to the interactive OAuth authorization-code flow (#142406).

The Desktop background MCP health sweep calls this endpoint every 30 minutes and on every
gateway connect, unattended. When the backend process has a console on stdin,
``_is_interactive()`` is true, so without suppression a rejected refresh token used to
continue into the authorization-code flow and ``webbrowser.open()`` a consent tab on the
backend host (observed: one tab every 30 minutes for ~6 hours). The endpoint must keep the
probe non-interactive and let the ``needs-auth`` / toast path do its job instead — the same
contract #109906 established for the gateway/cron/parked-probe paths.

Pins the wiring itself: a spy in place of ``_probe_single_server`` records the
``tools.mcp_oauth`` interactive flag as seen inside the probe.
"""

from __future__ import annotations

import pytest

from tools import mcp_oauth
from hermes_cli.web_routers import mcp as mcp_router


@pytest.mark.asyncio
async def test_dashboard_probe_runs_with_interactive_oauth_suppressed(monkeypatch):
    seen: dict = {}

    def fake_probe(name, cfg, details=None):
        seen["interactive"] = mcp_oauth._oauth_interactive_enabled.get()
        return [{"name": "echo", "description": "d"}]

    monkeypatch.setattr("hermes_cli.mcp_config._probe_single_server", fake_probe)
    monkeypatch.setattr(
        "hermes_cli.mcp_config._get_mcp_servers", lambda: {"srv": {"command": "echo"}}
    )
    monkeypatch.setattr(
        "tui_gateway.mcp_rpc_helpers.server_configs_with_sources",
        lambda config_servers: (config_servers, []),
    )

    result = await mcp_router.test_mcp_server("srv")

    assert result["ok"] is True
    assert seen["interactive"] is False


@pytest.mark.asyncio
async def test_dashboard_probe_reports_needs_auth_without_opening_a_browser(monkeypatch):
    """With suppression active, a probe that fails on auth surfaces as a normal probe
    failure (the caller maps it to needs-auth), not an OAuthException side channel that
    only the interactive flow produces."""
    seen: dict = {}

    def failing_probe(name, cfg, details=None):
        seen["interactive"] = mcp_oauth._oauth_interactive_enabled.get()
        raise RuntimeError("connection refused")

    monkeypatch.setattr("hermes_cli.mcp_config._probe_single_server", failing_probe)
    monkeypatch.setattr(
        "hermes_cli.mcp_config._get_mcp_servers", lambda: {"srv": {"command": "echo"}}
    )
    monkeypatch.setattr(
        "tui_gateway.mcp_rpc_helpers.server_configs_with_sources",
        lambda config_servers: (config_servers, []),
    )

    result = await mcp_router.test_mcp_server("srv")

    assert result["ok"] is False
    assert "connection refused" in result["error"]
    assert seen["interactive"] is False
