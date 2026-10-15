"""Policy gates on plugin-initiated tool calls (#138864).

``ctx.dispatch_tool`` and ``ctx.call_mcp`` used to reach the registry / MCP handler
directly, so a ``pre_tool_call`` policy plugin never saw calls a plugin made on the
model's behalf. Both now run the same guard order the model path uses: ``pre_tool_call``
hook, then ``tool_execution`` middleware. ``bypass_hooks=True`` keeps the raw path, and a
process that never discovered plugins keeps it too (no policy to run; forcing discovery
from a nested call would double-load the plugin set).
"""

import json
from unittest.mock import MagicMock

import pytest

from hermes_cli.plugins import PluginContext, PluginManifest, PluginManager
import hermes_cli.plugins as plugins_mod
import hermes_cli.middleware as middleware_mod


def _make_ctx(manager=None):
    manifest = PluginManifest(name="policy-plugin", key="policy-plugin", source="user")
    return PluginContext(manifest, manager or PluginManager())


@pytest.fixture
def discovered_manager(monkeypatch):
    """A plugin manager the policy gate treats as discovered: patched into the global slot
    (the official test seam) with ``_discovered=True`` so the gate never forces discovery."""
    mgr = PluginManager()
    mgr._discovered = True
    monkeypatch.setattr(plugins_mod, "_plugin_manager", mgr)
    return mgr


def _undiscovered(monkeypatch):
    """The gate's skip path: no discovered global manager (fresh cache, no single slot)."""
    monkeypatch.setattr(plugins_mod, "_plugin_manager", None)
    monkeypatch.setattr(plugins_mod, "_plugin_managers_by_home", {})


def _patch_pre_hook(monkeypatch, block=None, modified=None, captured=None):
    """Replace _dispatch_pre_tool_call_hooks; records its kwargs into ``captured``."""

    def _fake(tool_name, args, **hook_kwargs):
        if captured is not None:
            captured["tool_name"] = tool_name
            captured["args"] = args
            captured["ids"] = hook_kwargs
        return block, modified

    monkeypatch.setattr(plugins_mod, "_dispatch_pre_tool_call_hooks", _fake)


def _patch_middleware(monkeypatch, captured=None):
    """Transparent run_tool_execution_middleware that records the call and passes through."""

    def _fake(tool_name, args, next_call, **context):
        if captured is not None:
            captured["tool_name"] = tool_name
            captured["args"] = args
            captured["context_keys"] = sorted(context)
            captured["next_call"] = next_call
        return next_call(args)

    monkeypatch.setattr(middleware_mod, "run_tool_execution_middleware", _fake)


# ---------------------------------------------------------------------------
# dispatch_tool: pre_tool_call hook
# ---------------------------------------------------------------------------


class TestDispatchToolPreToolCall:
    def test_hook_fires_with_call_context_ids(self, monkeypatch, discovered_manager):
        captured = {}
        _patch_pre_hook(monkeypatch, captured=captured)
        mock_registry = MagicMock()
        mock_registry.dispatch.return_value = '{"ok": true}'
        cli = MagicMock()
        cli.agent.session_id = "sess-1"
        discovered_manager._cli_ref = cli
        ctx = _make_ctx(discovered_manager)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("tools.registry.registry", mock_registry)
            result = ctx.dispatch_tool("web_search", {"q": "hi"})

        assert result == '{"ok": true}'
        assert captured["tool_name"] == "web_search"
        assert captured["ids"]["session_id"] == "sess-1"
        assert captured["ids"]["tool_call_id"].startswith("plugin-")

    def test_blocked_call_returns_error_json_and_skips_dispatch(
        self, monkeypatch, discovered_manager
    ):
        _patch_pre_hook(monkeypatch, block="policy says no")
        mock_registry = MagicMock()
        discovered_manager._cli_ref = MagicMock()
        ctx = _make_ctx(discovered_manager)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("tools.registry.registry", mock_registry)
            result = ctx.dispatch_tool("shell_command", {"cmd": "ls"})

        assert json.loads(result)["error"] == "policy says no"
        mock_registry.dispatch.assert_not_called()

    def test_modified_args_flow_to_dispatch(self, monkeypatch, discovered_manager):
        _patch_pre_hook(monkeypatch, modified={"q": "rewritten"})
        mock_registry = MagicMock()
        mock_registry.dispatch.return_value = '{"ok": true}'
        ctx = _make_ctx(discovered_manager)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("tools.registry.registry", mock_registry)
            ctx.dispatch_tool("web_search", {"q": "original"})

        assert mock_registry.dispatch.call_args[0][1] == {"q": "rewritten"}

    def test_bypass_hooks_skips_the_chain(self, monkeypatch, discovered_manager):
        def _boom(*a, **k):
            raise AssertionError("hook must not fire with bypass_hooks=True")

        monkeypatch.setattr(plugins_mod, "_dispatch_pre_tool_call_hooks", _boom)
        mock_registry = MagicMock()
        mock_registry.dispatch.return_value = '{"ok": true}'
        ctx = _make_ctx(discovered_manager)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("tools.registry.registry", mock_registry)
            result = ctx.dispatch_tool("web_search", {"q": "hi"}, bypass_hooks=True)

        assert result == '{"ok": true}'
        mock_registry.dispatch.assert_called_once()

    def test_undiscovered_process_keeps_the_raw_path(self, monkeypatch):
        """No discovered global manager (e.g. a nested call served before boot discovery
        finished): the gate stands down instead of forcing a second plugin load."""
        _undiscovered(monkeypatch)

        def _boom(*a, **k):
            raise AssertionError(
                "hook must not fire when plugins were never discovered"
            )

        monkeypatch.setattr(plugins_mod, "_dispatch_pre_tool_call_hooks", _boom)
        mock_registry = MagicMock()
        mock_registry.dispatch.return_value = '{"ok": true}'
        ctx = _make_ctx(PluginManager())

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("tools.registry.registry", mock_registry)
            result = ctx.dispatch_tool("web_search", {"q": "hi"})

        assert result == '{"ok": true}'
        mock_registry.dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# dispatch_tool: tool_execution middleware
# ---------------------------------------------------------------------------


class TestDispatchToolExecutionMiddleware:
    def test_dispatch_runs_through_execution_middleware(
        self, monkeypatch, discovered_manager
    ):
        _patch_pre_hook(monkeypatch)
        mw = {}
        _patch_middleware(monkeypatch, captured=mw)
        mock_registry = MagicMock()
        mock_registry.dispatch.return_value = '{"ok": true}'
        ctx = _make_ctx(discovered_manager)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("tools.registry.registry", mock_registry)
            result = ctx.dispatch_tool("web_search", {"q": "hi"})

        assert result == '{"ok": true}'
        assert mw["tool_name"] == "web_search"
        assert mw["args"] == {"q": "hi"}
        assert "tool_call_id" in mw["context_keys"]
        # The terminal call inside the chain is the registry dispatch itself.
        assert mock_registry.dispatch.call_count == 1

    def test_blocked_call_never_reaches_middleware(
        self, monkeypatch, discovered_manager
    ):
        _patch_pre_hook(monkeypatch, block="denied")
        mw = {}
        _patch_middleware(monkeypatch, captured=mw)
        ctx = _make_ctx(discovered_manager)

        result = ctx.dispatch_tool("web_search", {"q": "hi"})

        assert json.loads(result)["error"] == "denied"
        assert mw == {}


# ---------------------------------------------------------------------------
# call_mcp: policy chain under the mcp__ wire name
# ---------------------------------------------------------------------------


def _patch_mcp(monkeypatch, response='{"result": "done"}', captured=None):
    from tools import mcp_tool_handlers as _mcp_handlers

    def _fake_make_handler(server_name, tool_name, tool_timeout):
        def _handler(args, **kwargs):
            if captured is not None:
                captured["server"] = server_name
                captured["tool"] = tool_name
                captured["handler_args"] = args
            return response

        return _handler

    monkeypatch.setattr(_mcp_handlers, "_make_tool_handler", _fake_make_handler)


def _patch_mcp_allowlist(monkeypatch, servers):
    import hermes_cli.config as config_mod

    monkeypatch.setattr(
        config_mod,
        "load_config",
        lambda *a, **k: {
            "plugins": {"entries": {"policy-plugin": {"mcp_allowlist": servers}}}
        },
    )


class TestCallMcpPolicyChain:
    def test_hook_sees_the_wire_name(self, monkeypatch, discovered_manager):
        _patch_mcp_allowlist(monkeypatch, ["github"])
        captured = {}
        _patch_pre_hook(monkeypatch, captured=captured)
        handler = {}
        _patch_mcp(monkeypatch, captured=handler)
        ctx = _make_ctx(discovered_manager)

        result = ctx.call_mcp("github", "create_issue", {"title": "x"})

        assert captured["tool_name"] == "mcp__github__create_issue"
        assert result["ok"] is True
        assert handler["handler_args"] == {"title": "x"}

    def test_blocked_call_returns_error_envelope_and_skips_handler(
        self, monkeypatch, discovered_manager
    ):
        _patch_mcp_allowlist(monkeypatch, ["github"])
        _patch_pre_hook(monkeypatch, block="mcp policy says no")
        handler = {}
        _patch_mcp(monkeypatch, captured=handler)
        ctx = _make_ctx(discovered_manager)

        result = ctx.call_mcp("github", "create_issue", {"title": "x"})

        assert result == {"ok": False, "error": "mcp policy says no"}
        assert handler == {}

    def test_bypass_hooks_keeps_the_raw_path(self, monkeypatch, discovered_manager):
        _patch_mcp_allowlist(monkeypatch, ["github"])

        def _boom(*a, **k):
            raise AssertionError("hook must not fire with bypass_hooks=True")

        monkeypatch.setattr(plugins_mod, "_dispatch_pre_tool_call_hooks", _boom)
        handler = {}
        _patch_mcp(monkeypatch, captured=handler)
        ctx = _make_ctx(discovered_manager)

        result = ctx.call_mcp(
            "github", "create_issue", {"title": "x"}, bypass_hooks=True
        )

        assert result["ok"] is True
        assert handler["handler_args"] == {"title": "x"}

    def test_modified_args_reach_the_handler(self, monkeypatch, discovered_manager):
        _patch_mcp_allowlist(monkeypatch, ["github"])
        _patch_pre_hook(monkeypatch, modified={"title": "rewritten"})
        handler = {}
        _patch_mcp(monkeypatch, captured=handler)
        ctx = _make_ctx(discovered_manager)

        ctx.call_mcp("github", "create_issue", {"title": "x"})

        assert handler["handler_args"] == {"title": "rewritten"}

    def test_undiscovered_process_keeps_the_raw_path(self, monkeypatch):
        _undiscovered(monkeypatch)
        _patch_mcp_allowlist(monkeypatch, ["github"])

        def _boom(*a, **k):
            raise AssertionError(
                "hook must not fire when plugins were never discovered"
            )

        monkeypatch.setattr(plugins_mod, "_dispatch_pre_tool_call_hooks", _boom)
        handler = {}
        _patch_mcp(monkeypatch, captured=handler)
        ctx = _make_ctx(PluginManager())

        result = ctx.call_mcp("github", "create_issue", {"title": "x"})

        assert result["ok"] is True
        assert handler["handler_args"] == {"title": "x"}


# ---------------------------------------------------------------------------
# End-to-end: a real registered pre_tool_call callback blocks a dispatch
# ---------------------------------------------------------------------------


class TestRealHookBlocksDispatch:
    def test_real_pre_tool_call_callback_blocks(self, discovered_manager):
        """A callback registered through the plugin manager's hook registry blocks
        ctx.dispatch_tool — proving the gate runs the real hook machinery, not a stub."""
        ctx = _make_ctx(discovered_manager)

        def _deny(tool_name, args, **kw):
            return {"action": "block", "message": "deny-listed"}

        handle = ctx.register_hook("pre_tool_call", _deny)
        mock_registry = MagicMock()
        try:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr("tools.registry.registry", mock_registry)
                result = ctx.dispatch_tool("web_search", {"q": "hi"})
        finally:
            handle.dispose()

        assert json.loads(result)["error"] == "deny-listed"
        mock_registry.dispatch.assert_not_called()
