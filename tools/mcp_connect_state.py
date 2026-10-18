"""Disk mirror of the per-server MCP connect backoff (#141030).

``tools.mcp_tool`` keeps its connect-failure count and retry deadline in module-level dicts,
so they die with the process that paid for them. But ``discover_mcp_tools()`` also runs in
many short-lived processes — every cron worker execution, one-shot CLI runs, Desktop backend
restarts — and each one started at failure count 0, re-spawning a permanently broken stdio
server on every run (one install logged ~5,000 launches of the same failing server and a
1 GB ``mcp-stderr.log``). This module mirrors the count and the deadline into one small JSON
file under the Hermes home so the next process inherits the backoff instead of the storm.

Deadlines are stored as wall clock (``time.time()``): the in-process stamps are
``time.monotonic()`` values, meaningless to any other process. All I/O is best-effort — a
missing, unreadable or corrupt file means no cross-process backoff, never a failed discovery.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from tools.mcp_tool_scope import ServerKey, _key_name, _key_scope

logger = logging.getLogger(__name__)

_STATE_FILENAME = "mcp-connect-backoff.json"
_KEY_SEP = (
    "\x1f"  # joined scope+name disk key; the separator cannot appear in a config path
)

# Read cache guarded by (path, mtime): one stat per consult instead of a read+parse per server.
# ``_save`` refreshes both, so this process always sees its own writes.
_cached_at: Optional[tuple[str, float]] = None
_cached_state: dict[str, dict[str, float]] = {}


def _state_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "cache" / _STATE_FILENAME


def _disk_key(key: ServerKey) -> str:
    scope = _key_scope(key)
    name = _key_name(key)
    return name if scope is None else f"{scope}{_KEY_SEP}{name}"


def _load() -> dict[str, dict[str, float]]:
    """Validated entries from the state file; ``{}`` when missing, unreadable or corrupt."""
    global _cached_at, _cached_state
    path = _state_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _cached_at, _cached_state = None, {}
        return _cached_state
    at = (str(path), mtime)
    if at == _cached_at:
        return _cached_state
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raw = None
    state: dict[str, dict[str, float]] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if (
                isinstance(k, str)
                and isinstance(v, dict)
                and isinstance(v.get("failures"), int)
                and not isinstance(v.get("failures"), bool)
                and isinstance(v.get("retry_after"), (int, float))
                and not isinstance(v.get("retry_after"), bool)
            ):
                state[k] = {
                    "failures": int(v["failures"]),
                    "retry_after": float(v["retry_after"]),
                }
    _cached_at, _cached_state = at, state
    return _cached_state


def _save(state: dict[str, dict[str, float]]) -> None:
    global _cached_at, _cached_state
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from utils import atomic_json_write

        atomic_json_write(path, state, indent=0, separators=(",", ":"))
        _cached_at, _cached_state = (str(path), path.stat().st_mtime), dict(state)
    except OSError:
        logger.debug(
            "MCP connect-backoff state write failed; falling back to per-process backoff"
        )


def entry_failures(key: ServerKey) -> int:
    """Consecutive connect failures another process already recorded for this connection."""
    entry = _load().get(_disk_key(key))
    return int(entry["failures"]) if entry else 0


def deadline(key: ServerKey) -> Optional[float]:
    """Wall-clock retry deadline another process stamped for this connection, if unexpired
    bookkeeping exists (expiry itself is the caller's ``time.time()`` comparison)."""
    entry = _load().get(_disk_key(key))
    return float(entry["retry_after"]) if entry else None


def stamp(key: ServerKey, failures: int, retry_after_wall: float) -> None:
    """Record *failures* and the wall-clock *retry_after* deadline for this connection."""
    state = dict(_load())
    state[_disk_key(key)] = {
        "failures": int(failures),
        "retry_after": float(retry_after_wall),
    }
    _save(state)


def discard(key: ServerKey) -> None:
    """Drop this connection's entry — a successful connect cleared the backoff everywhere."""
    state = dict(_load())
    disk_key = _disk_key(key)
    if disk_key in state:
        del state[disk_key]
        _save(state)
