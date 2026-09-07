"""delegate_task(background=true) on stateless API-server sessions.

Previously async_delivery_supported()=False forced SYNCHRONOUS execution for
every background dispatch on the API server, blocking the whole turn. Now
that background completions can wake the originating session via the
/v1/chat/completions self-post (gateway/wake.py), a session-continuable
turn (raw session id bound as the api_server chat_id) dispatches async; only
session-id-less one-shot requests keep the sync fallback.

The wake target must be captured from the request-scoped chat_id binding,
NOT from HERMES_SESSION_ID: constructing a child agent calls
set_current_session_id(child.session_id), clobbering the HERMES_SESSION_ID
ContextVar and os.environ with the subagent's internal id before the
dispatch code reads it — the fake child build below reproduces that clobber.
"""

import json
import time
from unittest.mock import MagicMock

import pytest

from gateway.session_context import set_session_vars
from tools.process_registry import process_registry


@pytest.fixture(autouse=True)
def _clean_queue_and_context(monkeypatch):
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    while not process_registry.completion_queue.empty():
        try:
            process_registry.completion_queue.get_nowait()
        except Exception:
            break
    yield
    # Restore ContextVars to the pristine "never set" sentinel rather than
    # clear_session_vars()'s explicit-"" state, which would mask env vars for
    # unrelated tests running later in the same worker.
    import gateway.session_context as sc

    for var in sc._VAR_MAP.values():
        var.set(sc._UNSET)
    sc._SESSION_ASYNC_DELIVERY.set(sc._UNSET)
    # wake-capable lives outside _VAR_MAP (no env fallback by design, #98619),
    # so reset it explicitly — a leaked "1" would hand the next test wake
    # authority its binder never declared.
    sc._SESSION_WAKE_CAPABLE.set(sc._UNSET)
    # set_current_session_id (invoked by the clobber-reproducing fake child
    # build) writes os.environ directly — scrub it so it can't leak into
    # other test modules.
    import os

    os.environ.pop("HERMES_SESSION_ID", None)
    while not process_registry.completion_queue.empty():
        try:
            process_registry.completion_queue.get_nowait()
        except Exception:
            break


def _drain_one(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not process_registry.completion_queue.empty():
            return process_registry.completion_queue.get_nowait()
        time.sleep(0.02)
    return None


def _fake_parent():
    parent = MagicMock()
    parent._delegate_depth = 0
    parent.session_id = "sess"
    parent._interrupt_requested = False
    parent._active_children = []
    parent._active_children_lock = None
    return parent


def _patch_delegate(monkeypatch):
    import tools.delegate_tool as dt

    fake_child = MagicMock()
    fake_child._delegate_role = "leaf"
    fake_child._subagent_id = "s1"

    def fast_child(task_index, goal, child=None, parent_agent=None, **kw):
        return {
            "task_index": 0, "status": "completed", "summary": f"done: {goal}",
            "api_calls": 1, "duration_seconds": 0.1, "model": "m",
            "exit_reason": "completed",
        }

    creds = {
        "model": "m", "provider": None, "base_url": None, "api_key": None,
        "api_mode": None, "command": None, "args": None,
    }
    def clobbering_build_child(**kw):
        # Reproduce what the real _build_child_agent -> AIAgent -> agent_init
        # path does: it synchronizes the child's internal session id into the
        # HERMES_SESSION_ID ContextVar + os.environ, clobbering the spawner's
        # id ~milliseconds before delegate_tool dispatches the batch.
        from gateway.session_context import set_current_session_id

        set_current_session_id("20260715_child1")
        return fake_child

    monkeypatch.setattr(dt, "_build_child_agent", clobbering_build_child)
    monkeypatch.setattr(dt, "_run_single_child", fast_child)
    monkeypatch.setattr(dt, "_resolve_delegation_credentials", lambda *a, **k: creds)
    return dt


def test_apiserver_session_with_id_dispatches_background(monkeypatch):
    """async_delivery=False + a raw session id (HERMES_SESSION_ID) that its binder
    DECLARED wake-capable (audited producer: explicit X-Hermes-Session-Id, a
    native /api/sessions id, a /v1/runs id — the client can address the id
    again) → background dispatch (the completion wakes the session via the
    api_server self-post), NOT the forced-sync fallback."""
    dt = _patch_delegate(monkeypatch)
    monkeypatch.setenv("HERMES_SESSION_ID", "raw-sid-7")
    set_session_vars(
        platform="api_server",
        chat_id="raw-sid-7",
        session_key="raw-sid-7",
        session_id="raw-sid-7",
        async_delivery=False,
        wake_capable="1",
    )

    out = dt.delegate_task(
        goal="bg on api_server", context="ctx",
        background=True, parent_agent=_fake_parent(),
    )
    parsed = json.loads(out)
    assert parsed["status"] == "dispatched", parsed
    assert parsed["mode"] == "background"

    evt = _drain_one()
    assert evt is not None
    assert evt["type"] == "async_delegation"
    # The raw session id is stamped so the gateway drain can self-post the
    # wake to the REAL session (session_key alone is the raw id here, which
    # carries no parseable routing metadata). Crucially this is the SPAWNER's
    # id, not the subagent-internal id the child build clobbered
    # HERMES_SESSION_ID with (see clobbering_build_child).
    assert evt["origin_session_id"] == "raw-sid-7"


# ---------------------------------------------------------------------------
# _current_origin_session_id — the clobber-proof origin capture helper
# ---------------------------------------------------------------------------


def test_apiserver_session_without_id_stays_synchronous(monkeypatch):
    """No session id to wake → keep the sync fallback (a detached result
    would never re-enter any conversation)."""
    dt = _patch_delegate(monkeypatch)
    set_session_vars(
        platform="api_server",
        chat_id="",
        session_key="",
        session_id="",
        async_delivery=False,
    )

    out = dt.delegate_task(
        goal="one-shot", context="ctx",
        background=True, parent_agent=_fake_parent(),
    )
    parsed = json.loads(out)
    assert parsed.get("status") != "dispatched", parsed
    assert "SYNCHRONOUSLY" in parsed.get("note", "")
    assert process_registry.completion_queue.empty()


# ---------------------------------------------------------------------------
# #98619 — wake capability must be DECLARED, never assumed
# ---------------------------------------------------------------------------


def test_apiserver_wake_capability_omission_fails_closed(monkeypatch):
    """#98619 public-omission regression: a binder that binds a raw session id
    but NEVER declares wake capability (set_session_vars called without the
    flag — exactly what a future, not-yet-audited binding path would do) must
    NOT get background dispatch. Wake authority is proof-carrying: omitted
    means denied, and the batch stays synchronous."""
    dt = _patch_delegate(monkeypatch)
    set_session_vars(
        platform="api_server",
        chat_id="raw-sid-undeclared",
        session_key="raw-sid-undeclared",
        session_id="raw-sid-undeclared",
        async_delivery=False,
    )

    out = dt.delegate_task(
        goal="bg undeclared", context="ctx",
        background=True, parent_agent=_fake_parent(),
    )
    parsed = json.loads(out)
    assert parsed.get("status") != "dispatched", parsed
    assert "SYNCHRONOUSLY" in parsed.get("note", "")
    assert process_registry.completion_queue.empty()


def test_apiserver_derived_session_id_stays_synchronous(monkeypatch):
    """#98619: a bound-but-derived chat id (header-less OpenAI-compatible
    client, no X-Hermes-Session-Id) must NOT dispatch background — the wake
    self-post would hard-fail (no API_SERVER_KEY) or land in a session whose
    history the client never reloads. A session id merely existing is not
    wake-capable; the binding must declare it."""
    dt = _patch_delegate(monkeypatch)
    set_session_vars(
        platform="api_server",
        chat_id="fingerprint-hex-0123456789abcdef",
        session_key="fingerprint-hex-0123456789abcdef",
        session_id="fingerprint-hex-0123456789abcdef",
        wake_capable="",
        async_delivery=False,
    )

    out = dt.delegate_task(
        goal="bg derived", context="ctx",
        background=True, parent_agent=_fake_parent(),
    )
    parsed = json.loads(out)
    assert parsed.get("status") != "dispatched", parsed
    assert "SYNCHRONOUSLY" in parsed.get("note", "")
    assert process_registry.completion_queue.empty()


def test_wake_capable_session_reader_is_default_deny(monkeypatch):
    """#98619: wake_capable_session() True ONLY for the literal "1". _UNSET
    (never declared) and "" (declared not capable) both fail closed, and a
    leaked HERMES_SESSION_WAKE_CAPABLE env var must NOT grant authority —
    the reader never falls back to os.environ."""
    import gateway.session_context as sc
    from gateway.session_context import wake_capable_session

    # Never bound in this context: _UNSET sentinel, no env fallback even when
    # the variable is present in the environment.
    monkeypatch.setenv("HERMES_SESSION_WAKE_CAPABLE", "1")
    assert wake_capable_session() is False

    # Declared wake-capable (audited producer): "1".
    tokens = set_session_vars(platform="api_server", wake_capable="1")
    assert wake_capable_session() is True
    sc.clear_session_vars(tokens)
    # A cleared context has declared nothing: back to denied.
    assert wake_capable_session() is False

    # Explicitly declared NOT wake-capable (fingerprint-derived id): "".
    tokens = set_session_vars(platform="api_server", wake_capable="")
    assert wake_capable_session() is False
    sc.clear_session_vars(tokens)
