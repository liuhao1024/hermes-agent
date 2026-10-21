"""skill_view repeat-view dedup registry: per-task cache of (resolved skill name, file_path) ->
digest of the skill_view result served. A repeat view whose result is byte-identical returns a short
stub — the earlier tool result already carries the content verbatim. Content digests need no file
timestamps, so a same-size rewrite in one coarse mtime tick (or on a host whose clock disagrees with
the filesystem's) is never mistaken for "unchanged". Cleared via ``reset_skill_view_dedup()`` on
context compression AND on a committed proactive tool-result prune, because both replace the
original content with a one-line marker.
"""

import hashlib
import json
import threading

_skill_view_tracker: dict[str, dict[tuple, str]] = {}
_skill_view_tracker_lock = threading.Lock()
_SKILL_VIEW_DEDUP_CAP = 200

_SKILL_VIEW_DEDUP_MESSAGE = (
    "Skill content unchanged since it was loaded earlier in this "
    "conversation — refer to the earlier skill_view result; it is still "
    "current and complete. (Re-issued after context compression, this "
    "returns the full content again.)")


def _dedup_key_digest(task_id, name, file_path, payload: dict, result: str):
    """Shared admission + fingerprint of one serve: None when it must never take part in
    dedup (no task to key it to, or setup-needed readiness a re-view must re-show)."""
    if (not task_id or payload.get("setup_needed")
            or payload.get("readiness_status") == "setup_needed"):
        return None
    return ((str(payload.get("name") or name), file_path or ""),
            hashlib.sha256(result.encode("utf-8")).hexdigest())


def _remember(cache, key, digest) -> None:
    cache.pop(key, None)  # re-insert at the end: FIFO eviction keeps the recently served
    cache[key] = digest
    while len(cache) > _SKILL_VIEW_DEDUP_CAP:  # FIFO eviction
        del cache[next(iter(cache))]


def record_skill_view(task_id, name, file_path, payload: dict, result: str) -> None:
    """Record a successful skill_view serve from ANY caller — the tool handler, or a plugin /
    preload path that imported ``skill_view`` directly (#142427) — so a byte-identical repeat
    view from the tool returns the unchanged stub instead of the full body again."""
    if (kd := _dedup_key_digest(task_id, name, file_path, payload, result)) is None:
        return
    key, digest = kd
    with _skill_view_tracker_lock:
        _remember(_skill_view_tracker.setdefault(str(task_id), {}), key, digest)


def dedup_skill_view(task_id, name, file_path, payload: dict, result: str) -> str | None:
    """The "unchanged" stub when this task was already served exactly ``result`` for this skill
    file; otherwise record ``result``'s digest and return None (serve the full result)."""
    if (kd := _dedup_key_digest(task_id, name, file_path, payload, result)) is None:
        return None
    key, digest = kd
    with _skill_view_tracker_lock:
        cache = _skill_view_tracker.setdefault(str(task_id), {})
        if cache.get(key) == digest:
            return json.dumps({
                "success": True, "status": "unchanged", "name": str(payload.get("name") or name),
                "file": file_path or "SKILL.md", "dedup": True, "content_returned": False,
                "message": _SKILL_VIEW_DEDUP_MESSAGE}, ensure_ascii=False)
        _remember(cache, key, digest)
    return None


def reset_skill_view_dedup(task_id: str | None = None) -> None:
    """Clear the dedup cache (all tasks when task_id is None); called on context compression."""
    with _skill_view_tracker_lock:
        if task_id is None:
            _skill_view_tracker.clear()
        else:
            _skill_view_tracker.pop(str(task_id), None)
