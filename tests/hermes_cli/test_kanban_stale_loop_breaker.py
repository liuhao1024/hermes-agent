"""Regressions: the stale-reap path must feed the unified failure breaker.

A worker killed before its FIRST heartbeat (e.g. an unreachable serving
endpoint hanging the connect/retry block) reaps deterministically: spawn →
4 h stale → requeue, forever, with ``consecutive_failures`` pinned at 0 —
#145676 observed 27 loops over 4.5 days on one card. These tests pin the new
contract: a never-heartbeated stale run ticks the shared counter and
auto-blocks at the configured limit with a ``stale-loop`` reason, while a
heartbeating-then-stalled run keeps the benign reclaim semantics (an absent
heartbeat is not a worker fault — healthy long runners must never trip the
breaker through this path). The counter is DB-resident, so it survives
restarts, and only a meaningful success clears it.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd


@pytest.fixture
def conn(tmp_path: Path):
    db = kbc.connect(tmp_path / "kanban.db")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def no_kill(monkeypatch):
    monkeypatch.setattr(
        kbd, "_terminate_reclaimed_worker", lambda *a, **k: {"terminated": True})


def _stale_running_card(conn, title: str, *, heartbeat):
    """A claimed, pid-bound card whose active run started 2 h ago."""
    task_id = kb.create_task(conn, title=title, assignee="builder")
    claimed = kb.claim_task(conn, task_id)
    assert claimed is not None
    kbd._set_worker_pid(conn, task_id, 98765)
    start = int(time.time()) - 7200
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE tasks SET started_at = ?, last_heartbeat_at = ? WHERE id = ?",
            (start, heartbeat, task_id),
        )
        conn.execute(
            "UPDATE task_runs SET started_at = ? WHERE id = ?",
            (start, claimed.current_run_id),
        )
    return task_id


def test_stale_loop_never_heartbeated_blocks_after_failure_limit(conn, no_kill):
    """The #145676 loop: each never-heartbeated stale lap ticks the counter and
    the second lap trips the breaker into ``blocked`` with a ``stale-loop``
    reason; a bare requeue in between must not reset it."""
    task_id = _stale_running_card(conn, "endpoint outage loop", heartbeat=None)

    # Lap 1: reaped, counter moves to 1, card goes back to ready — no gave_up yet.
    assert kbd.detect_stale_running(conn, stale_timeout_seconds=1, failure_limit=2) == [task_id]
    task = kb.get_task(conn, task_id)
    assert task.status == "ready"
    assert task.consecutive_failures == 1
    assert (task.last_failure_error or "").startswith("stale-loop:")
    assert [e.kind for e in kb.list_events(conn, task_id=task_id)][-1] == "stale"

    # Lap 2 (the bare requeue above did NOT reset the counter): limit reached,
    # the breaker auto-blocks on top of the stale event.
    assert kb.claim_task(conn, task_id) is not None
    start = int(time.time()) - 7200
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE tasks SET started_at = ?, last_heartbeat_at = NULL WHERE id = ?",
            (start, task_id),
        )
        conn.execute(
            "UPDATE task_runs SET started_at = ? WHERE id = ?",
            (start, kb.get_task(conn, task_id).current_run_id),
        )
    assert kbd.detect_stale_running(conn, stale_timeout_seconds=1, failure_limit=2) == [task_id]
    task = kb.get_task(conn, task_id)
    assert task.status == "blocked"
    assert task.consecutive_failures == 2

    gave_up = [e for e in kb.list_events(conn, task_id=task_id) if e.kind == "gave_up"][-1]
    assert gave_up.payload["trigger_outcome"] == "stale"
    assert gave_up.payload["effective_limit"] == 2
    assert gave_up.payload["limit_source"] == "dispatcher"
    assert gave_up.payload["error"].startswith("stale-loop:")


def test_stale_with_heartbeat_does_not_tick_breaker(conn, no_kill):
    """A run that heartbeated and then stalled keeps the benign reclaim: no
    counter movement, no block — healthy long runners stay exempt."""
    task_id = _stale_running_card(
        conn, "stalled after heartbeat", heartbeat=int(time.time()) - 7200)

    assert kbd.detect_stale_running(conn, stale_timeout_seconds=1, failure_limit=2) == [task_id]
    task = kb.get_task(conn, task_id)
    assert task.status == "ready"
    assert task.consecutive_failures == 0
    assert task.last_failure_error is None
    assert [e.kind for e in kb.list_events(conn, task_id=task_id)][-1] == "stale"


def test_stale_loop_counter_resets_on_real_success(conn, no_kill):
    """The only reset is a meaningful success: after a stale tick, completing
    the card clears the counter (worker and gateway restarts must NOT)."""
    task_id = _stale_running_card(conn, "loop then success", heartbeat=None)
    assert kbd.detect_stale_running(conn, stale_timeout_seconds=1, failure_limit=2) == [task_id]
    assert kb.get_task(conn, task_id).consecutive_failures == 1

    assert kb.complete_task(conn, task_id, result="endpoint recovered")
    task = kb.get_task(conn, task_id)
    assert task.consecutive_failures == 0
    assert task.last_failure_error is None
