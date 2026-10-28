"""Regression tests for issue #146213.

``get_tool_definitions(quiet_mode=True)`` memoizes under ``registry._generation``,
which only moves on registry mutations. An MCP server that drops and regains its
session flips every MCP check_fn back to True without any mutation, so a
tool-def list computed while the server was offline (check_fn False, tools
absent) stayed pinned for every later session. Two layers pinned here:

- ``MCPServerTask.session`` None -> session bumps the registry generation
- MCP check_fns skip the 30 s TTL verdict read (a False from the gap must not
  outlive the reconnect) while keeping grace/logging unchanged
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import model_tools
from tools.mcp_tool import MCPServerTask
from tools.mcp_tool_common import _core
from tools.mcp_tool_handlers import _make_check_fn
from tools.mcp_tool_registration import _register_server_tools
from tools.registry import ToolRegistry, _check_fn_cache, _check_fn_cached


def _make_mcp_tool(name: str):
    return SimpleNamespace(
        name=name, description="", inputSchema={"type": "object", "properties": {}}
    )


@pytest.fixture
def registry():
    # model_tools binds the registry singleton at import time, while registration
    # paths resolve it lazily — patch both bindings so every layer sees the same
    # isolated instance.
    fresh = ToolRegistry()
    with patch("tools.registry.registry", fresh), patch("model_tools.registry", fresh):
        yield fresh
    model_tools._tool_defs_cache.clear()
    _check_fn_cache.clear()


def test_regained_session_bumps_generation(registry):
    server = MCPServerTask("srv")
    base = registry._generation

    server.session = MagicMock()
    assert registry._generation == base + 1

    # Replacing a live session is not a verdict flip: no bump.
    server.session = MagicMock()
    assert registry._generation == base + 1

    # Dropping the session is not a bump either: a short reconnect keeps serving the
    # cached list; a long outage removes the tools via parking.
    server.session = None
    assert registry._generation == base + 1


def test_regained_session_unpins_stale_tool_defs(registry):
    server = MCPServerTask("srv")
    server._tools = [_make_mcp_tool("read")]
    server.session = MagicMock()
    _core._servers["srv"] = server
    try:
        server._registered_tool_names = _register_server_tools("srv", server, {})
        schema = "mcp__srv__read"

        def names():
            return {
                t["function"]["name"]
                for t in model_tools.get_tool_definitions(
                    enabled_toolsets=["mcp-srv"],
                    quiet_mode=True,
                    skip_tool_search_assembly=True,
                )
            }

        # Connection drops before any list is built; a list computed in this window
        # omits the tools and is memoized under the current generation.
        server.session = None
        assert schema not in names()

        # Reconnected. Re-registration is a no-op: the tools never left the registry,
        # so only the generation bump below can unpin the stale list.
        server.session = MagicMock()
        server._register_discovered_tools_if_needed()

        assert schema in names()
    finally:
        _core._servers.pop("srv", None)


def test_check_fn_false_from_gap_does_not_outlive_reconnect(registry):
    server = MCPServerTask("srv")
    server.session = None
    _core._servers["srv"] = server
    try:
        check = _make_check_fn("srv")
        assert _check_fn_cached(check) is False

        server.session = MagicMock()
        # The False verdict stored during the gap must not be served from the TTL
        # store: MCP probes read in-memory state only.
        assert _check_fn_cached(check) is True
    finally:
        _core._servers.pop("srv", None)
        _check_fn_cache.clear()
