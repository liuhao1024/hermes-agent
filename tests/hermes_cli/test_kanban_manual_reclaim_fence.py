"""A manual (operator/DM) reclaim must not release a live worker's claim.

``reclaim_task`` terminates the worker and, on ``main``, releases the claim
without checking that the worker actually died. A Python subprocess mid-LLM-call
commonly survives SIGTERM briefly, so the release spawns a second worker beside
the still-alive first one. The TTL path (``release_stale_claims``) and the
heartbeat-stale reaper already fence this; these tests pin the same fence on the
manual path — plus the deliberate exception: an UNVERIFIED fingerprint stays a
human override (it can never be safely signalled, so it is released on request).
"""

import os
import signal
import time

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    conn = kbc.connect(tmp_path / "kanban.db")
    try:
        yield conn
    finally:
        conn.close()


def _live_worker_task(conn, *, unverified: bool = False) -> str:
    """A claimed running card whose worker row points at this live test process."""
    tid = kb.create_task(conn, title="job", assignee="worker")
    kb.claim_task(conn, tid)
    if unverified:
        with kb.write_txn(conn):
            conn.execute(
                "UPDATE tasks SET worker_pid = ?, worker_started_at = ? WHERE id = ?",
                (os.getpid(), kbd.UNVERIFIED_WORKER_FINGERPRINT, tid))
    else:
        kbd._set_worker_pid(conn, tid, os.getpid())
    return tid


def _stub_worker_liveness(monkeypatch, *, alive_after_kill: bool):
    """Make the recorded worker look like a process that outlives SIGTERM/SIGKILL."""
    monkeypatch.setattr(kb, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(kbd, "_worker_alive", lambda pid, started_at: alive_after_kill)
    monkeypatch.setattr(kbd, "_poll_worker_exit", lambda pid, started_at: False)


def test_surviving_worker_keeps_its_claim_on_manual_reclaim(board, monkeypatch):
    conn = board
    tid = _live_worker_task(conn)
    _stub_worker_liveness(monkeypatch, alive_after_kill=True)
    before = kb.get_task(conn, tid)
    assert before.status == "running" and before.claim_lock

    killed = []
    ok = kb.reclaim_task(conn, tid, reason="operator",
                         signal_fn=lambda pid, sig: killed.append((pid, sig)))

    assert ok is False                                   # reclaim refused
    assert (os.getpid(), signal.SIGTERM) in killed       # the kill was genuinely attempted
    after = kb.get_task(conn, tid)
    assert after.status == "running"                     # claim held, not released
    assert after.claim_lock == before.claim_lock
    # The claim's TTL was pushed out to a fresh defer-grace window (>= 110s of the 120s grace).
    assert after.claim_expires >= int(time.time()) + kb.RECLAIM_DEFER_GRACE_SECONDS - 10
    ev = conn.execute(
        "SELECT kind, payload FROM task_events WHERE task_id = ? AND kind = 'reclaim_deferred' "
        "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    assert ev is not None
    assert "manual_reclaim_worker_alive" in ev["payload"]


def test_dead_worker_is_reclaimed_normally(board, monkeypatch):
    conn = board
    tid = _live_worker_task(conn)
    _stub_worker_liveness(monkeypatch, alive_after_kill=False)

    killed = []
    assert kb.reclaim_task(conn, tid, reason="operator",
                           signal_fn=lambda pid, sig: killed.append((pid, sig))) is True
    assert killed == [(os.getpid(), signal.SIGTERM)]     # no SIGKILL escalation needed
    after = kb.get_task(conn, tid)
    assert after.status == "ready"
    assert after.claim_lock is None
    assert after.worker_pid is None


def test_reassign_inherits_the_fence(board, monkeypatch):
    conn = board
    tid = _live_worker_task(conn)
    _stub_worker_liveness(monkeypatch, alive_after_kill=True)
    killed = []
    # reassign_task does not thread a signal_fn through to its reclaim: stub the kill
    # backend so the reclaim's default ``os.kill`` cannot signal this very test process.
    monkeypatch.setattr(
        kbd, "_kill_fn", lambda signal_fn: lambda pid, sig: killed.append((pid, sig)))

    assert kb.reassign_task(conn, tid, "dev", reclaim_first=True, reason="operator") is False
    assert killed and killed[0][1] == signal.SIGTERM
    after = kb.get_task(conn, tid)
    assert after.status == "running"                     # never reassigned away from the live worker
    assert after.claim_lock is not None


def test_unverified_fingerprint_stays_human_override(board, monkeypatch):
    conn = board
    tid = _live_worker_task(conn, unverified=True)
    monkeypatch.setattr(kb, "_pid_alive", lambda pid: True)

    killed = []
    assert kb.reclaim_task(conn, tid, reason="operator",
                           signal_fn=lambda pid, sig: killed.append((pid, sig))) is True
    assert killed == []                                  # never signalled by bare number
    assert kb.get_task(conn, tid).status == "ready"


def _refused_kill(pid, sig):
    # Windows: signalling an exited child raises plain OSError (often
    # PermissionError), not ProcessLookupError.
    raise PermissionError(13, "Permission denied")


def test_windows_oserror_on_dead_worker_still_reclaims(board, monkeypatch):
    """An exited worker misreported by the OSError path must not defer forever."""
    conn = board
    tid = _live_worker_task(conn)
    _stub_worker_liveness(monkeypatch, alive_after_kill=False)

    assert kb.reclaim_task(conn, tid, reason="operator",
                           signal_fn=_refused_kill) is True
    after = kb.get_task(conn, tid)
    assert after.status == "ready"                       # reclaimed without waiting for TTL
    assert after.claim_lock is None


def test_windows_oserror_on_live_worker_still_defers(board, monkeypatch):
    """A live worker whose signal was denied keeps the duplicate-spawn fence."""
    conn = board
    tid = _live_worker_task(conn)
    _stub_worker_liveness(monkeypatch, alive_after_kill=True)

    assert kb.reclaim_task(conn, tid, reason="operator",
                           signal_fn=_refused_kill) is False
    after = kb.get_task(conn, tid)
    assert after.status == "running"                     # claim held
    assert after.claim_lock is not None
