"""Policy gates for plugin-initiated tool calls (#138864).

``PluginContext.dispatch_tool`` and ``PluginContext.call_mcp`` historically went straight
to the registry / MCP handler, so a ``pre_tool_call`` policy plugin (approvals, budgets,
allowlists) saw every model-initiated call but none of the calls another plugin made on
the model's behalf. :func:`policy_gated_tool_call` wraps those entry points in the same
guard order the model path uses (``model_tools.handle_function_call``): ``pre_tool_call``
hook, then ``tool_execution`` middleware, then dispatch.

Also hosts the ``ctx.call_mcp`` result envelope, moved here from ``hermes_cli.plugins``
to offset that file's FILE_LINES budget (moved code keeps its cap).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

MCP_RESULT_CHAR_CAP = 65536


def _call_ids(cli_ref: Any) -> dict[str, str]:
    """Identity fields for one plugin-initiated tool call (``None`` -> ``""`` wire contract).

    ``session_id``/``task_id`` come from the calling context's agent when one is attached
    (interactive CLI); gateway/worker contexts have no ``_cli_ref`` and report ``""``.
    ``tool_call_id`` is generated per call so policy plugins can tell plugin-initiated
    calls apart (``plugin-`` prefix) and never collide with model-issued ids.
    """
    agent = getattr(cli_ref, "agent", None)
    return {
        "task_id": getattr(agent, "task_id", None) or "",
        "session_id": getattr(agent, "session_id", None) or "",
        "turn_id": "",
        "api_request_id": "",
        "tool_call_id": f"plugin-{uuid.uuid4().hex[:16]}",
    }


def _gate_available() -> bool:
    """True when the global plugin manager has been discovered.

    A process that never discovered plugins has no policy hooks to run, and forcing discovery
    from inside a nested plugin call would double-load the plugin set — under host isolation
    that spawns a second host process, so a handler's ``ctx.dispatch_tool`` lands on the wrong
    one (#138864). Real surfaces (CLI, gateway, cron) discover at boot; undiscovered embedders
    keep the raw path.
    """
    try:
        from hermes_cli.plugins import get_plugin_manager

        return bool(getattr(get_plugin_manager(), "_discovered", False))
    except Exception:  # health: allow BLE001 -- probe must fail open to the raw path, never break the dispatch
        return False


def policy_gated_tool_call(
    cli_ref: Any,
    tool_name: str,
    args: dict[str, Any],
    dispatch: Callable[[dict[str, Any]], Any],
) -> tuple[Any, Optional[str]]:
    """Run one plugin-initiated tool call through the policy chain.

    Returns ``(dispatch_result, None)`` after ``pre_tool_call`` (args may be modified by the
    hook) and ``tool_execution`` middleware wrapping ``dispatch``, or ``(None, block_message)``
    when a ``pre_tool_call`` hook blocked the call. Hook failure is fail-open like the model
    path (``model_tools._pre_dispatch_guards``); ``pre_tool_call`` timeouts still fail closed
    inside ``invoke_hook`` (``plugins_dispatch._HOOK_TIMEOUT_FAIL_CLOSED_HOOKS``).
    """
    if not _gate_available():
        return dispatch(args), None
    ids = _call_ids(cli_ref)
    try:
        # Same-module lookups stay lazy: hermes_cli.plugins imports this file at top level.
        from hermes_cli.plugins import _dispatch_pre_tool_call_hooks

        block_message, modified_args = _dispatch_pre_tool_call_hooks(
            tool_name, args, **ids
        )
    except Exception as hook_err:
        logger.debug(
            "pre_tool_call hook error on plugin dispatch of %s: %s", tool_name, hook_err,
            exc_info=True,
        )
        block_message, modified_args = None, None
    if block_message is not None:
        return None, block_message
    if modified_args is not None:
        args = modified_args
    from hermes_cli.middleware import run_tool_execution_middleware

    result = run_tool_execution_middleware(
        tool_name, args, dispatch, original_args=dict(args), **ids
    )
    return result, None


def mcp_allowlist_for(plugin_id: str) -> list[str]:
    """Operator-granted MCP server allowlist; missing/unreadable -> [] (default-deny)."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
    except Exception:  # health: allow BLE001 -- config torn mid-edit must stay default-deny, not crash the caller
        return []
    from hermes_cli.plugins_state import _plugin_settings_entry

    allowlist = (_plugin_settings_entry(cfg, plugin_id) or {}).get("mcp_allowlist")
    return [str(item) for item in allowlist] if isinstance(allowlist, list) else []


def mcp_result_envelope(raw: Any) -> dict[str, Any]:
    """Normalize an MCP handler result string into a stable envelope."""
    if not isinstance(raw, str):
        raw = "" if raw is None else str(raw)
    truncated = len(raw) > MCP_RESULT_CHAR_CAP
    if truncated:
        raw = raw[:MCP_RESULT_CHAR_CAP] + "… [truncated]"
    try:
        parsed: Any = json.loads(raw)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict) and "error" in parsed:
        envelope: dict[str, Any] = {"ok": False, "error": parsed["error"]}
    elif isinstance(parsed, dict) and "result" in parsed:
        envelope = {"ok": True, "result": parsed["result"]}
        if "structuredContent" in parsed:
            envelope["structuredContent"] = parsed["structuredContent"]
    else:
        envelope = {"ok": True, "result": parsed if parsed is not None else raw}
    return {**envelope, "truncated": True} if truncated else envelope
