"""Invariant: a failed liveness probe is unknown, not death (#141758).

Under load the per-pid probes themselves fail transiently — macOS ``ps``
exits non-zero under timeout pressure, ``psutil``'s ``create_time`` read
raises. Each failure used to be read as a definitive negative: a live
worker was booked ``crashed: pid not alive``, its claim released (the
retry spawned a second reviewer beside the still-running original) and
the original was later SIGTERM'd by the terminal reaper. A probe failure
must keep the standing answer instead: the crash sweep holds the task,
and a worker whose identity cannot be read is never signalled on that
evidence alone.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway import status as gateway_status
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
# The sleeping-fixture child is shared with the terminal-reaper suite: both
# need a real live pid whose identity the dispatcher can fingerprint.
from tests.hermes_cli.test_kanban_terminal_worker_reaper import _sleeper


@pytest.fixture
def conn(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    kb.init_db()
    with kbc.connect() as c:
        yield c


def test_unreadable_identity_is_not_recycled(conn, monkeypatch):
    """A live pid whose start time cannot be read is unknown, not foreign:
    ``_worker_alive`` keeps the standing answer for both fingerprint shapes."""
    proc = _sleeper()
    try:
        fingerprint = kbd._process_fingerprint(proc.pid)
        assert fingerprint is not None  # the spawn-time read worked
        # Transient probe failure on a later tick (psutil raised under load).
        monkeypatch.setattr(gateway_status, "get_process_start_time", lambda _pid: None)

        assert kbd._pid_recycled(proc.pid, fingerprint) is False
        assert kbd._pid_recycled(proc.pid, 12345) is False  # legacy integer shape
        assert kbd._worker_alive(proc.pid, fingerprint) is True
        assert kbd._worker_alive(proc.pid, 12345) is True
    finally:
        proc.kill()
        proc.wait()


def test_crash_sweep_holds_a_live_worker_when_the_probe_fails(conn, monkeypatch):
    """The reclaim sweep must not book ``crashed: pid not alive`` on one
    unreadable probe: the task stays ``running`` beside its live worker."""
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    proc = _sleeper()
    try:
        host = kb._claimer_id().split(":", 1)[0]
        tid = kb.create_task(conn, title="probe", assignee="a")
        kb.claim_task(conn, tid, claimer=f"{host}:w0")
        kbd._set_worker_pid(conn, tid, proc.pid)  # records the real fingerprint
        monkeypatch.setattr(gateway_status, "get_process_start_time", lambda _pid: None)

        assert kbd.detect_crashed_workers(conn) == []

        task = kb.get_task(conn, tid)
        assert task.status == "running"
        assert task.worker_pid == proc.pid
        outcomes = [
            r["outcome"] for r in conn.execute(
                "SELECT outcome FROM task_runs WHERE task_id=?", (tid,),
            ).fetchall()
        ]
        assert "crashed" not in outcomes
    finally:
        proc.kill()
        proc.wait()


def test_darwin_ps_failure_keeps_the_existence_answer(conn, monkeypatch):
    """On macOS ``ps -o stat=`` only answers the zombie question: a non-zero
    exit (timeout under load) must not overturn the existence answer. A read
    that succeeds and shows ``Z`` still reports dead."""
    proc = _sleeper()
    try:
        assert kbd._pid_alive(proc.pid) is True  # healthy probe on a live pid

        def failing_ps(*_a, **_k):
            return SimpleNamespace(returncode=1, stdout="")

        def zombie_ps(*_a, **_k):
            return SimpleNamespace(returncode=0, stdout="Z")

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(kbd.subprocess, "run", failing_ps)
        assert kbd._pid_alive(proc.pid) is True

        monkeypatch.setattr(kbd.subprocess, "run", zombie_ps)
        assert kbd._pid_alive(proc.pid) is False
    finally:
        proc.kill()
        proc.wait()


def test_reaper_refuses_to_signal_an_unreadable_identity(conn, monkeypatch):
    """The terminal reaper never signals a worker whose identity it could not
    read (probe failure ≠ recycled PID); the evidence stays for a later tick
    instead of being discarded as a recycled stranger."""
    proc = _sleeper()
    signals: list[tuple[int, int]] = []

    def signal_fn(pid, sig):
        signals.append((pid, sig))
        import os
        os.kill(pid, sig)

    try:
        tid = kb.create_task(conn, title="done", assignee="coder")
        kb.claim_task(conn, tid, claimer=kb._claimer_id())
        run_id = kb._current_run_id(conn, tid)
        kbd._set_worker_pid(conn, tid, proc.pid)
        assert kb.complete_task(conn, tid, result="done", expected_run_id=run_id) is True
        conn.execute("UPDATE task_runs SET ended_at = ended_at - 600 WHERE id=?", (run_id,))
        monkeypatch.setattr(gateway_status, "get_process_start_time", lambda _pid: None)

        assert kbd.reap_terminal_workers(conn, signal_fn=signal_fn) == []

        assert signals == [] and proc.poll() is None
        run = conn.execute("SELECT worker_pid FROM task_runs WHERE id=?", (run_id,)).fetchone()
        assert run["worker_pid"] == proc.pid  # kept for a later tick

        termination = kbd._terminate_reclaimed_worker(
            proc.pid, kb._claimer_id(), signal_fn=signal_fn,
            started_at=kbd._process_fingerprint(proc.pid) or kbd.UNVERIFIED_WORKER_FINGERPRINT,
        )
        assert termination["signal_refused"] is True
        assert termination["terminated"] is False
        assert signals == []
    finally:
        proc.kill()
        proc.wait()
