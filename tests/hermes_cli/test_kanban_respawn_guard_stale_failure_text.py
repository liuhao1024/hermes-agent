"""A review handoff supersedes the stale ``last_failure_error`` diagnosis.

The dead-worker sweep stamps a quota-flavored ``last_failure_error`` on a
rate-limited requeue *without* counting a failure. When the respawned worker
later succeeds and hands the card off, the review transitions end the run with
``review_requested``/``changes_requested`` — so ``check_respawn_guard``'s
rate-limit branch no longer applies, and its quota/auth check matches the
stale "quota wall" text and parks the card as ``blocker_auth`` forever: the
reviewer (or, after changes are requested, the re-dispatched implementer)
never spawns until an operator runs ``hermes kanban unblock`` (#144512).

A review transition is success-grade progress for the failure *text*: it
supersedes a diagnosis written before the run that produced the reviewed work
(the same way ``complete_task`` clears it). ``consecutive_failures`` is
deliberately untouched — a review transition is not evidence the pathology
cleared, per the ``request_changes`` contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an empty kanban DB."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _exited_status(code: int) -> int:
    """Raw wait-status for a WIFEXITED child with the given exit code."""
    return code << 8


@pytest.fixture
def rate_limited_requeue(kanban_home, monkeypatch):
    """A card the dead-worker sweep requeued after a quota-wall exit.

    Drives the real sweep over a dead worker that exited
    ``KANBAN_RATE_LIMIT_EXIT_CODE``, so the card carries exactly what
    production carries into the next respawn: a ``rate_limited`` run, the
    stamped "quota wall" error text, and a ``ready`` status — with
    ``consecutive_failures`` non-zero from an earlier crash, proving below
    that the counter survives the review transitions.
    """
    monkeypatch.setattr(kb, "_pid_alive", lambda _pid: False)
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    # Freeze the clock for the sweep, then advance past the rate-limit
    # cooldown for the handoff, so the review run's ended_at strictly
    # supersedes the rate_limited run's (same-second ties would leave the
    # "latest ended run" ambiguous).
    monkeypatch.setattr(kb.time, "time", lambda: 5_000_000.0)
    with kbc.connect() as conn:
        host = kb._claimer_id().split(":", 1)[0]
        tid = kb.create_task(conn, title="stale-quota", assignee="a")
        kb.claim_task(conn, tid, claimer=f"{host}:w0")
        pid = 71000
        conn.execute(
            "UPDATE tasks SET worker_pid = ?, consecutive_failures = 1 WHERE id = ?",
            (pid, tid),
        )
        conn.commit()
        kbd._record_worker_exit(pid, _exited_status(kb.KANBAN_RATE_LIMIT_EXIT_CODE))
        assert tid not in kbd.detect_crashed_workers(conn)
        task = kb.get_task(conn, tid)
        assert task.status == "ready"
        assert task.consecutive_failures == 1
        assert task.last_failure_error and "rate-limited" in task.last_failure_error
        return tid


def test_review_handoff_supersedes_stale_quota_text(rate_limited_requeue, monkeypatch):
    """``request_review`` clears the stale diagnosis so the reviewer spawns."""
    monkeypatch.setattr(kb.time, "time", lambda: 5_000_400.0)
    tid = rate_limited_requeue
    with kbc.connect() as conn:
        claimed = kb.claim_task(conn, tid)
        assert claimed is not None
        assert kb.request_review(
            conn, tid, summary="done", reviewer="r",
            expected_run_id=claimed.current_run_id,
        )
        task = kb.get_task(conn, tid)
        assert task.last_failure_error is None
        assert task.consecutive_failures == 1  # deliberately preserved
        assert kbd.check_respawn_guard(conn, tid, lane="review") is None


def test_changes_requested_supersedes_stale_quota_text(rate_limited_requeue, monkeypatch):
    """The implementer → review → changes loop re-dispatches the implementer."""
    monkeypatch.setattr(kb.time, "time", lambda: 5_000_400.0)
    tid = rate_limited_requeue
    with kbc.connect() as conn:
        claimed = kb.claim_task(conn, tid)
        assert claimed is not None
        assert kb.request_review(
            conn, tid, summary="done", reviewer="r",
            expected_run_id=claimed.current_run_id,
        )
        reviewed = kb.claim_review_task(conn, tid)
        assert reviewed is not None
        ok, res = kb.request_changes(
            conn, tid, reason="the summary skips the edge case",
            expected_run_id=reviewed.current_run_id,
        )
        assert ok, res
        task = kb.get_task(conn, tid)
        assert task.last_failure_error is None
        assert kbd.check_respawn_guard(conn, tid) is None
