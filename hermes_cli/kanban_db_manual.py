"""Manual reclaim/reassign: the operator-triggered claim-release paths.

Split out of ``hermes_cli.kanban_db``; origin-resident helpers are reached
late-bound via ``_kb`` (import-cycle breaking) so monkeypatching
``kanban_db.<name>`` keeps working.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Optional


def reclaim_task(
    conn: sqlite3.Connection, task_id: str, *, reason: Optional[str] = None, signal_fn=None,
) -> bool:
    """Operator reclaim regardless of TTL: release the claim, restore the source
    phase, reset the failure counter. False when not running — or when our own
    worker survived the kill (the claim is held, like ``release_stale_claims``)."""
    row = conn.execute(
        "SELECT status, claim_lock, worker_pid, worker_started_at FROM tasks WHERE id = ?", (task_id,),
    ).fetchone()
    if not row:
        return False
    if row["status"] != "running" and row["claim_lock"] is None:
        # Nothing to reclaim — already ready / blocked / done.
        return False
    prev_lock = row["claim_lock"]
    termination = _kb._terminate_reclaimed_worker(
        row["worker_pid"], prev_lock, signal_fn=signal_fn, started_at=row["worker_started_at"])
    # A live worker of ours must keep its claim (else a duplicate spawns beside it) — the
    # same fence as ``release_stale_claims`` / the heartbeat-stale reaper, but only when we
    # actually signalled the worker: an UNVERIFIED fingerprint stays a human override.
    if _kb._worker_survived_termination(termination) and termination.get("termination_attempted"):
        _kb._defer_reclaim_for_live_worker(
            conn, task_id, prev_lock, int(time.time()), termination,
            reason="manual_reclaim_worker_alive",
        )
        return False
    with _kb.write_txn(conn):
        retry_status = _kb._retry_status_for_run(conn, task_id)
        cur = conn.execute(
            """UPDATE tasks SET status = ?, claim_lock = NULL,
               claim_expires = NULL, worker_pid = NULL, worker_started_at = NULL
               WHERE id = ? AND status IN ('running', 'ready', 'blocked')
               AND claim_lock IS ?""",
            (retry_status, task_id, prev_lock),
        )
        if cur.rowcount != 1:
            return False
        _kb._record_reclaim(
            conn, task_id, termination,
            error=f"manual_reclaim: {reason}" if reason else f"manual_reclaim lock={prev_lock}",
            payload={"manual": True, "reason": reason, "prev_lock": prev_lock, "retry_status": retry_status},
        )
    # Operator intervention = fresh retry budget (own txn, runs after commit).
    _kb._clear_failure_counter(conn, task_id)
    return True


def reassign_task(
    conn: sqlite3.Connection, task_id: str, profile: Optional[str], *, reclaim_first: bool = False,
    reason: Optional[str] = None,
) -> bool:
    """Reassign (None unassigns); a running task is refused unless
    ``reclaim_first`` releases its claim — the "this profile's model is broken" path."""
    if reclaim_first:
        # Safe to call even if nothing to reclaim.
        reclaim_task(conn, task_id, reason=reason or "reassign")
    # assign_task handles its own txn + the still-running guard.
    try:
        return _kb.assign_task(conn, task_id, profile)
    except RuntimeError:
        # Task is still running and reclaim_first was False; caller
        # needs to decide whether to retry with reclaim.
        return False


# Late-bound origin namespace (see module docstring); imported LAST so this
# module is fully populated before ``kanban_db`` imports from it.
from hermes_cli import kanban_db as _kb
