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

_skill_view_tracker: dict[str, dict[tuple, tuple[str, str]]] = {}
_skill_view_tracker_lock = threading.Lock()
_SKILL_VIEW_DEDUP_CAP = 200

_SKILL_VIEW_DEDUP_MESSAGE = (
    "Skill content unchanged since it was loaded earlier in this "
    "conversation — refer to the earlier skill_view result; it is still "
    "current and complete. (Re-issued after context compression, this "
    "returns the full content again.)"
)

_SKILL_VIEW_AUTO_LOAD_MESSAGE = (
    "Skill content unchanged since it was auto-loaded via skills.auto_load "
    "at session start — refer to the auto-loaded block earlier in this "
    "conversation; it is still current and complete. (Re-issued after "
    "context compression, this returns the full content again.)"
)


def _remember_view(task_id, resolved: str, file_path, result: str, source: str) -> None:
    """Record ``result``'s digest for (resolved, file_path) under *task_id*; FIFO eviction
    keeps the recently served entries. Caller must hold ``_skill_view_tracker_lock``."""
    key = (resolved, file_path or "")
    digest = hashlib.sha256(result.encode("utf-8")).hexdigest()
    cache = _skill_view_tracker.setdefault(str(task_id), {})
    entry = (digest, source)
    if cache.get(key) == entry:
        return
    cache.pop(
        key, None
    )  # re-insert at the end: FIFO eviction keeps the recently served
    cache[key] = entry
    while len(cache) > _SKILL_VIEW_DEDUP_CAP:  # FIFO eviction
        del cache[next(iter(cache))]


def dedup_skill_view(
    task_id, name, file_path, payload: dict, result: str
) -> str | None:
    """The "unchanged" stub when this task was already served exactly ``result`` for this skill
    file (by skill_view, or by the session-start skills.auto_load render); otherwise record
    ``result``'s digest and return None (serve the full result)."""
    # Never dedup setup-needed views: readiness depends on config/env state that
    # changes without the file changing; the model must see the refreshed status.
    if (
        not task_id
        or payload.get("setup_needed")
        or payload.get("readiness_status") == "setup_needed"
    ):
        return None
    resolved = str(payload.get("name") or name)
    key = (resolved, file_path or "")
    digest = hashlib.sha256(result.encode("utf-8")).hexdigest()
    with _skill_view_tracker_lock:
        cache = _skill_view_tracker.setdefault(str(task_id), {})
        entry = cache.get(key)
        if entry is not None and entry[0] == digest:
            message = (
                _SKILL_VIEW_AUTO_LOAD_MESSAGE
                if entry[1] == "auto_load"
                else _SKILL_VIEW_DEDUP_MESSAGE
            )
            return json.dumps(
                {
                    "success": True,
                    "status": "unchanged",
                    "name": resolved,
                    "file": file_path or "SKILL.md",
                    "dedup": True,
                    "content_returned": False,
                    "message": message,
                },
                ensure_ascii=False,
            )
        _remember_view(task_id, resolved, file_path, result, "view")
    return None


def record_preloaded_skill_view(
    task_id, name, file_path, payload: dict, result: str
) -> None:
    """Register content already rendered into the session prompt (skills.auto_load) as a
    served view: a later byte-identical skill_view returns the auto-load stub instead of
    duplicating the full content. Best-effort — a failure never blocks session start;
    ``result`` must be rendered the way skill_view serves it (preprocess on) so the
    digests match."""
    try:
        if (
            not task_id
            or payload.get("setup_needed")
            or payload.get("readiness_status") == "setup_needed"
        ):
            return
        resolved = str(payload.get("name") or name)
        _remember_view(task_id, resolved, file_path, result, "auto_load")
    # health: allow BLE001, S110 -- best-effort dedup bookkeeping; never blocks session start (#145280)
    except Exception:
        pass


def reset_skill_view_dedup(task_id: str | None = None) -> None:
    """Clear the dedup cache (all tasks when task_id is None); called on context compression."""
    with _skill_view_tracker_lock:
        if task_id is None:
            _skill_view_tracker.clear()
        else:
            _skill_view_tracker.pop(str(task_id), None)
