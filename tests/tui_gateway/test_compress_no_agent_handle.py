"""Tests for manual-compress routes on a session with no agent handle.

A session restored from state.db carries ``session["agent"] = None`` until its
first turn builds one (#145137). Every manual-compress route must then refuse
cleanly — never hand ``None`` to the compression core, never let the
context-engine notification helpers' ``setattr(None, ...)`` raise inside an
``except`` path (that AttributeError replaced the real error), and never
answer the slash mirror with a silent empty string.
"""
import threading

import pytest


def _make_session():
    return {
        "agent": None,
        "history_lock": threading.Lock(),
        "history": [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
        ],
        "history_version": 1,
        "running": False,
        "session_key": "sess-no-agent",
    }


def test_finalize_without_agent_is_a_safe_noop():
    """Both finalize paths run inside callers' except blocks: a None agent must
    return False instead of raising AttributeError from setattr(None, ...)."""
    from agent.conversation_compression import finalize_context_engine_compression_notification

    assert finalize_context_engine_compression_notification(None, committed=False) is False
    assert finalize_context_engine_compression_notification(None, committed=True) is False


def test_queue_without_agent_stages_nothing():
    """Staging a deferred notification on a None agent is the same no-op as an
    unregistered engine — it must not raise on setattr(None, ...)."""
    from agent.conversation_compression import _queue_context_engine_compression_notification

    _queue_context_engine_compression_notification(
        None, new_session_id="new", old_session_id="old")


def test_notify_without_agent_returns_false():
    from agent.conversation_compression import _notify_context_engine_compression_complete

    assert _notify_context_engine_compression_complete(
        None, new_session_id="new", old_session_id="old") is False


def test_compress_session_history_refuses_without_agent():
    """The shared choke point refuses with a clean message instead of handing
    None to compress_now (whose failure used to surface as the notification
    attribute's AttributeError)."""
    from tui_gateway.server import _compress_session_history

    session = _make_session()
    with pytest.raises(ValueError, match="No active agent"):
        _compress_session_history(session)


def test_compress_slash_mirror_refuses_without_agent():
    """The slash mirror used to answer "" — the silent (no output) symptom;
    it must return the same clean refusal text as the other no-agent paths."""
    from tui_gateway.methods_slash import _NO_AGENT, _SLASH_MIRRORS

    result = _SLASH_MIRRORS["compress"]("sid", _make_session(), None, "")
    assert result == _NO_AGENT
    assert result  # non-empty: the mirror's text reaches the user
