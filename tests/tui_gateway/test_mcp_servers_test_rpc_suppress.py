"""Regression test: the TUI gateway's ``mcp.servers.test`` RPC probe must never fall through
to the interactive OAuth authorization-code flow (#142406).

The gateway process usually has a console on stdin (``hermes dashboard`` in a terminal, or a
scheduled task via ``cmd /c``), so ``_is_interactive()`` is true and the Desktop health sweep
driving this RPC runs unattended. Without suppression a rejected refresh token used to
continue into the authorization-code flow and open a consent tab on the backend host; the
endpoint must keep the probe non-interactive and let the response's ``oauth_needed`` /
``needs-auth`` path do its job instead — the same contract #109906 established for the
gateway/cron/parked-probe paths.

Pins the wiring itself: a spy in place of ``_probe_single_server`` records the
``tools.mcp_oauth`` interactive flag as seen inside the probe.
"""

from __future__ import annotations

import pytest

from tui_gateway import methods_tools, server
from tools import mcp_oauth
from tui_gateway.method_ctx import rebind


def _servers_test_handler():
    """The registered handler, rebound onto server.py's globals like install() does —
    bodies reference server globals bare, so the pending function is not callable as-is."""
    for name, fn in methods_tools._registry._pending:
        if name == "mcp.servers.test":
            return rebind(fn, vars(server))
    raise AssertionError("mcp.servers.test handler not registered")


def _stub_discovery(monkeypatch, probe):
    monkeypatch.setattr("hermes_cli.mcp_config._probe_single_server", probe)
    monkeypatch.setattr(
        "hermes_cli.mcp_config._get_mcp_servers", lambda: {"srv": {"command": "echo"}}
    )
    monkeypatch.setattr(
        "tui_gateway.mcp_rpc_helpers.server_configs_with_sources",
        lambda config_servers: (config_servers, []),
    )


def test_rpc_probe_runs_with_interactive_oauth_suppressed(monkeypatch):
    seen: dict = {}

    def fake_probe(name, cfg, details=None):
        seen["interactive"] = mcp_oauth._oauth_interactive_enabled.get()
        return [("echo", "d")]

    _stub_discovery(monkeypatch, fake_probe)

    response = _servers_test_handler()("rid-1", {"name": "srv"})

    assert seen["interactive"] is False
    assert response["result"]["ok"] is True
    assert response["result"]["tools"] == [{"name": "echo", "description": "d"}]


def test_rpc_probe_reports_failure_without_opening_a_browser(monkeypatch):
    seen: dict = {}

    def failing_probe(name, cfg, details=None):
        seen["interactive"] = mcp_oauth._oauth_interactive_enabled.get()
        raise RuntimeError("connection refused")

    _stub_discovery(monkeypatch, failing_probe)

    response = _servers_test_handler()("rid-1", {"name": "srv"})

    assert seen["interactive"] is False
    assert response["result"]["ok"] is False
    assert "connection refused" in response["result"]["error"]
