"""Invariant: ``complete_task`` never closes a live worker's run for a caller that
neither owns the run nor asked for an operator override (issue #111764).

A claim-less completion (a human at the CLI, an orchestrator session — anything
without ``HERMES_KANBAN_*`` env) used to be authorised by task status alone, so it
marked a ``running`` card done and ``_end_run`` closed the dispatcher worker's run
row while that worker kept executing. The guard mirrors ``request_review``'s: a
``running`` task under a live claim needs ``expected_run_id`` or ``force=True``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli import kanban_db_connect as kbc


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


def _claimed_running_task(conn, *, live_worker: bool = True) -> tuple[str, int]:
    tid = kb.create_task(conn, title="live", assignee="coder")
    assert kb.claim_task(conn, tid, claimer=kb._claimer_id()) is not None
    if live_worker:
        # This process stands in for the spawned worker: alive, fingerprinted.
        kbd._set_worker_pid(conn, tid, os.getpid())
    return tid, kb._current_run_id(conn, tid)


def test_claimless_complete_refuses_live_run_until_forced(conn):
    tid, run_id = _claimed_running_task(conn)

    with pytest.raises(kb.LiveClaimError):
        kb.complete_task(conn, tid, result="someone else says done")

    # Nothing moved: the worker's run is still open and it can still finish its own card.
    run = conn.execute("SELECT ended_at FROM task_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["ended_at"] is None
    assert conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()["status"] == "running"
    assert kb.complete_task(conn, tid, result="worker done", expected_run_id=run_id) is True

    # Explicit operator override still closes a live run.
    tid2, run2 = _claimed_running_task(conn)
    assert kb.complete_task(conn, tid2, result="operator override", force=True) is True
    run = conn.execute("SELECT ended_at, outcome FROM task_runs WHERE id = ?", (run2,)).fetchone()
    assert run["ended_at"] is not None and run["outcome"] == "completed"


def test_claimless_complete_of_claim_without_live_worker_unchanged(conn):
    """A claim whose worker never spawned (or is gone) protects no live run: the
    library / CLI flow that claims and then completes keeps working."""
    tid, run_id = _claimed_running_task(conn, live_worker=False)
    assert kb.complete_task(conn, tid, result="done") is True
    run = conn.execute("SELECT ended_at FROM task_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["ended_at"] is not None


def test_claimless_complete_of_unclaimed_card_unchanged(conn):
    """The legitimate manual flow — completing a card nobody is working on — needs no proof."""
    tid = kb.create_task(conn, title="admin", assignee="coder")
    assert kb.complete_task(conn, tid, result="done") is True
    assert conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()["status"] == "done"


def test_request_review_shares_the_live_worker_fence(conn):
    """``request_review`` keys on the same liveness as ``complete_task``: a claim
    without a live worker process is not a live claim (the human/library flow
    ``claim`` -> ``request_review`` works), a live worker's claim still is."""
    tid, _ = _claimed_running_task(conn, live_worker=False)
    assert kb.request_review(conn, tid, summary="handoff") is True
    assert kb.get_task(conn, tid).status == "review"

    tid2, run2 = _claimed_running_task(conn)
    ok, reason = kb.request_review(conn, tid2, summary="steal", with_reason=True)
    assert ok is False and "live claim" in reason
    assert kb.request_review(conn, tid2, summary="own", expected_run_id=run2) is True


# ---------------------------------------------------------------------------
# The unexpired-claim-lease fence (issue #145739): a claim taken without a
# spawned worker (`kanban claim`, or any control-plane lane calling claim_task)
# holds an unexpired lease that _claim_is_live ignores. The fence is opt-in at
# the library layer (library callers share the process that claimed); the CLI
# turns it on because a separate CLI invocation is a distinct principal.
# ---------------------------------------------------------------------------


def test_lease_fence_refuses_stranger_complete_unless_proven(conn):
    tid, run_id = _claimed_running_task(conn, live_worker=False)
    lock = kb.get_task(conn, tid).claim_lock

    with pytest.raises(kb.ClaimLeaseError):
        kb.complete_task(conn, tid, result="stranger says done",
                         enforce_claim_ownership=True)

    # Nothing moved: the lease-holder's run is still open.
    run = conn.execute("SELECT ended_at FROM task_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["ended_at"] is None
    assert conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()["status"] == "running"

    # Presenting the printed lock proves ownership.
    assert kb.complete_task(conn, tid, result="holder done", claim_lock=lock,
                            enforce_claim_ownership=True) is True

    # The claimant's own run id also proves it (control-plane lanes hold a run).
    tid2, run2 = _claimed_running_task(conn, live_worker=False)
    with pytest.raises(kb.ClaimLeaseError):
        kb.complete_task(conn, tid2, result="stranger again", enforce_claim_ownership=True)
    assert kb.complete_task(conn, tid2, result="run owner", expected_run_id=run2,
                            enforce_claim_ownership=True) is True

    # force stays the explicit operator override.
    tid3, _ = _claimed_running_task(conn, live_worker=False)
    assert kb.complete_task(conn, tid3, result="operator override", force=True,
                            enforce_claim_ownership=True) is True


def test_lease_fence_ignores_lapsed_lease_and_missing_lock_columns(conn):
    """A lapsed lease protects nothing (the card is reclaimable), and a foreign
    lock does not satisfy the fence."""
    tid, _ = _claimed_running_task(conn, live_worker=False)
    conn.execute("UPDATE tasks SET claim_expires = ? WHERE id = ?",
                 (int(time.time()) - 1, tid))
    conn.commit()
    assert kb.complete_task(conn, tid, result="after lapse",
                            enforce_claim_ownership=True) is True

    tid2, _ = _claimed_running_task(conn, live_worker=False)
    with pytest.raises(kb.ClaimLeaseError):
        kb.complete_task(conn, tid2, result="wrong lock", claim_lock="other-host:1",
                         enforce_claim_ownership=True)


def test_request_review_shares_the_lease_fence(conn):
    tid, _ = _claimed_running_task(conn, live_worker=False)
    lock = kb.get_task(conn, tid).claim_lock

    ok, reason = kb.request_review(conn, tid, summary="steal", with_reason=True,
                                   enforce_claim_ownership=True)
    assert ok is False and "lease" in reason
    assert kb.get_task(conn, tid).status == "running"

    assert kb.request_review(conn, tid, summary="holder handoff", claim_lock=lock,
                             enforce_claim_ownership=True) is True
    assert kb.get_task(conn, tid).status == "review"


# ---------------------------------------------------------------------------
# CLI surface: `claim` prints the lock it took; `complete` / `request-review`
# enforce the lease fence and accept `--claim-lock` / $HERMES_KANBAN_CLAIM_LOCK.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    kb.init_db()
    return home


def _ready_task() -> str:
    from hermes_cli import kanban_db_connect as kbc_cli
    with kbc_cli.connect_closing() as c:
        return kb.create_task(c, title="leased", assignee="coder")


def _grab_claim_lock(claimed: str) -> str:
    return next(
        line.split("Claim lock: ", 1)[1].strip()
        for line in claimed.splitlines() if line.startswith("Claim lock: ")
    )


def test_cli_claim_prints_lock_and_complete_requires_it(cli_home):
    from hermes_cli import kanban as kc

    tid = _ready_task()
    claimed = kc.run_slash(f"claim {tid}")
    assert f"Claimed {tid}" in claimed
    lock = _grab_claim_lock(claimed)

    refused = kc.run_slash(f'complete {tid} --result "stranger says done"')
    assert "unexpired claim lease" in refused
    from hermes_cli import kanban_db_connect as kbc_cli
    with kbc_cli.connect_closing() as c:
        assert kb.get_task(c, tid).status == "running"

    allowed = kc.run_slash(f"complete {tid} --result done --claim-lock {lock}")
    assert f"Completed {tid}" in allowed


def test_cli_complete_honours_claim_lock_env(cli_home, monkeypatch):
    from hermes_cli import kanban as kc

    tid = _ready_task()
    lock = _grab_claim_lock(kc.run_slash(f"claim {tid}"))
    monkeypatch.setenv("HERMES_KANBAN_CLAIM_LOCK", lock)
    allowed = kc.run_slash(f'complete {tid} --result "holder done"')
    assert f"Completed {tid}" in allowed


def test_cli_request_review_enforces_the_lease_fence(cli_home):
    from hermes_cli import kanban as kc

    tid = _ready_task()
    lock = _grab_claim_lock(kc.run_slash(f"claim {tid}"))

    refused = kc.run_slash(f"request-review {tid} --summary steal")
    assert "unexpired claim lease" in refused

    allowed = kc.run_slash(f"request-review {tid} --summary handoff --claim-lock {lock}")
    assert f"Requested review for {tid}" in allowed
