"""agent-browser may print a literal ``null`` (a no-op ``back`` with no history) — the parsed
``None`` must be reported as a failed command, not leak into the ``result.get(...)`` chain
(#141209). Code-path invariant; runs on every host without a browser."""

from __future__ import annotations

from tools import browser_tool_session as bt_session


def _run_through_seam(monkeypatch, command: str, stdout: str) -> dict:
    """Drive ``_run_browser_command`` with the spawn faked at ``_spawn_and_collect``; the result
    dict the tool's callers would see is returned (the crash was at the first ``.get()`` on it)."""
    monkeypatch.setattr(bt_session, "_browser_command_preflight", lambda: {"browser_cmd": "agent-browser"})
    monkeypatch.setattr(bt_session, "_get_session_info", lambda task_id: {"session_name": "s", "cdp_url": None})
    monkeypatch.setattr(bt_session._cloud, "_get_browser_engine", lambda: "auto")
    monkeypatch.setattr(bt_session._cloud, "_is_headed_mode", lambda: False)

    def fake_spawn(task_id, session_info, cmd_parts, cmd, engine, timeout, stdin_payload=None):
        return bt_session._interpret_browser_command_output(cmd, stdout, "", 0)

    monkeypatch.setattr(bt_session, "_spawn_and_collect", fake_spawn)
    return bt_session._run_browser_command("t", command, [], timeout=5)


def test_null_output_becomes_a_failed_command_not_a_crash(monkeypatch):
    """``back`` answering ``null`` (no history) returns ``{success: False, ...}`` through the full
    dispatch — pre-fix this raised ``AttributeError: 'NoneType' object has no attribute 'get'``."""
    result = _run_through_seam(monkeypatch, "back", "null")
    assert result == {"success": False, "error": "Browser command 'back' returned no output", "returncode": 0}


def test_parsed_json_still_passes_through(monkeypatch):
    """Real command output keeps flowing untouched: success stays success, failure dicts keep
    their backend-provided error, so the null guard did not widen."""
    ok = _run_through_seam(monkeypatch, "back", '{"success":true,"data":{"url":"https://example.com/"}}')
    assert ok == {"success": True, "data": {"url": "https://example.com/"}}
    failed = _run_through_seam(monkeypatch, "back", '{"success":false,"error":"no tab"}')
    assert failed == {"success": False, "error": "no tab"}
