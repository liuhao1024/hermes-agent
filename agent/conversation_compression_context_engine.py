"""Deferred context-engine compression notifications: stage an exactly-once hook call for
an outer host transaction, then emit or discard it at finalize time. Split from
conversation_compression.py for the FILE_LINES ratchet — that module re-exports these
names, so every caller keeps its import path and nothing changes for patching observers.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_PENDING_CONTEXT_ENGINE_NOTIFICATION = "_pending_context_engine_compression_notification"


def _hand_off_metrics_segment(old_session_id: str, new_session_id: str) -> None:
    """Shared metrics count one conversation across the rotation (a no-op when collection is off)."""
    try:
        from hermes_cli.observability.relay_shared_metrics import rotate_segment
        rotate_segment(old_session_id, new_session_id)
    except Exception:
        logger.debug('shared-metrics segment hand-off failed', exc_info=True)


def _notify_context_engine_compression_complete(agent: Any, *, new_session_id: str, old_session_id: str) -> bool:
    """Notify the active context engine after a durable compression commit."""
    if agent is None:
        # No agent handle lives in this process (compute-host surface, or a session restored
        # from state.db before its first turn) — there is no engine to notify (#145137).
        return False
    # Opt-in relay session-span segmentation. Observer semantics — failure must
    # never undo or delay the committed compression.
    try:
        from agent import relay_runtime
        relay_runtime.SESSION_COORDINATOR.notify_session_compacted(
            profile_key=relay_runtime.current_profile_key(), session_id=new_session_id, old_session_id=old_session_id
        )
    except Exception:
        logger.debug('relay segment rotation notification failed', exc_info=True)
    _hand_off_metrics_segment(old_session_id, new_session_id)
    callback = getattr(agent.context_compressor, "on_session_start", None)
    if not callable(callback):
        return False
    try:
        callback(
            new_session_id, boundary_reason="compression", old_session_id=old_session_id,
            platform=getattr(agent, "platform", None) or "cli",
            conversation_id=getattr(agent, "_gateway_session_key", None),
        )
        return True
    except Exception:
        # Context-engine hooks are observers. A callback failure must not undo
        # history that the core or an outer host transaction already committed.
        logger.debug("context engine on_session_start (compression) failed", exc_info=True)
        return False


def _queue_context_engine_compression_notification(agent: Any, *, new_session_id: str, old_session_id: str) -> None:
    """Stage exactly one existing hook call for an outer host transaction."""
    if agent is None:
        # Same no-op as an unregistered engine: setattr(None, ...) would raise and, on the
        # error path, replace the compaction failure that actually needs reporting (#145137).
        return
    if callable(getattr(agent, _PENDING_CONTEXT_ENGINE_NOTIFICATION, None)):
        raise RuntimeError("a compression notification is already pending")

    def _notify() -> bool:
        return _notify_context_engine_compression_complete(
            agent, new_session_id=new_session_id, old_session_id=old_session_id
        )

    setattr(agent, _PENDING_CONTEXT_ENGINE_NOTIFICATION, _notify)


def finalize_context_engine_compression_notification(agent: Any, *, committed: bool) -> bool:
    """Emit or discard a deferred notification; repeated calls are no-ops."""
    if agent is None:
        # Nothing can be staged without an agent handle, and this runs inside callers'
        # except paths — failing here would mask the original error (#145137).
        return False
    pending = getattr(agent, _PENDING_CONTEXT_ENGINE_NOTIFICATION, None)
    setattr(agent, _PENDING_CONTEXT_ENGINE_NOTIFICATION, None)
    if not committed or not callable(pending):
        return False
    return bool(pending())
