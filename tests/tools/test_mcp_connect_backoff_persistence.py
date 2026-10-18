"""Regression test for #141030: the MCP connect backoff must survive its process.

``_server_connect_failures`` / ``_server_connect_retry_after`` are module-level dicts, so a
process that paid for a failing stdio server's backoff took the cooldown with it when it
exited. ``discover_mcp_tools()`` runs in many short-lived processes (every cron worker
execution, one-shot CLI runs, Desktop backend restarts); each new one started at failure
count 0 and re-spawned the broken server again — thousands of launches and identical
tracebacks per day. The fix mirrors the count and a wall-clock deadline into
``cache/mcp-connect-backoff.json`` so the next process consults it.
"""

import json
import time

import pytest

import tools.mcp_tool as mcp_mod
from tools import mcp_connect_state as _connect_state
from tools import mcp_tool_discovery as _mcp_discovery


@pytest.fixture(autouse=True)
def _reset_backoff_state():
    """Snapshot the in-process cooldown dicts and give this test a clean disk state file."""
    snapshot = (
        dict(mcp_mod._server_connect_failures),
        dict(mcp_mod._server_connect_retry_after),
    )
    mcp_mod._server_connect_failures.clear()
    mcp_mod._server_connect_retry_after.clear()
    state_file = _connect_state._state_path()
    try:
        state_file.unlink()
    except FileNotFoundError:
        pass
    _connect_state._cached_at, _connect_state._cached_state = None, {}
    try:
        yield
    finally:
        (failures, retry_after) = snapshot
        mcp_mod._server_connect_failures.clear()
        mcp_mod._server_connect_failures.update(failures)
        mcp_mod._server_connect_retry_after.clear()
        mcp_mod._server_connect_retry_after.update(retry_after)
        _connect_state._cached_at, _connect_state._cached_state = None, {}


def _forget_in_process_state():
    """Simulate the next short-lived process: fresh module dicts, disk state intact."""
    mcp_mod._server_connect_failures.clear()
    mcp_mod._server_connect_retry_after.clear()
    _connect_state._cached_at, _connect_state._cached_state = None, {}


class TestCrossProcessBackoff:
    def test_next_process_inherits_cooldown(self):
        _mcp_discovery._record_connect_failure("broken")

        _forget_in_process_state()
        assert not mcp_mod._server_connect_retry_after, (
            "precondition: this process forgot"
        )
        assert _mcp_discovery._connect_cooldown_active("broken") is True, (
            "a fresh process re-armed the failing server's cooldown from scratch -- "
            "restart storm not contained across processes (#141030)"
        )

    def test_failure_count_accumulates_across_processes(self):
        for _ in range(3):
            _mcp_discovery._record_connect_failure("broken")
            _forget_in_process_state()

        now = 1000.0
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(time, "monotonic", lambda: now)
            mp.setattr(time, "time", lambda: 5000.0)
            _mcp_discovery._record_connect_failure("broken")
        assert mcp_mod._server_connect_failures["broken"] == 4, (
            "each process restarted the geometric ladder from rung 1"
        )
        entry = json.loads(_connect_state._state_path().read_text())["broken"]
        assert entry["failures"] == 4
        assert entry["retry_after"] == pytest.approx(5000.0 + 30.0 * 2**3)

    def test_successful_connect_clears_disk_state(self):
        _mcp_discovery._record_connect_failure("broken")

        _mcp_discovery._clear_connect_failure("broken")
        _forget_in_process_state()

        assert _mcp_discovery._connect_cooldown_active("broken") is False, (
            "a server that connected once must not stay cooldown-armed for other processes"
        )

    def test_expired_wall_clock_deadline_permits_retry(self):
        _mcp_discovery._record_connect_failure("broken")
        path = _connect_state._state_path()
        state = json.loads(path.read_text())
        state["broken"]["retry_after"] = time.time() - 1
        path.write_text(json.dumps(state))

        _forget_in_process_state()
        assert _mcp_discovery._connect_cooldown_active("broken") is False

    def test_corrupt_state_file_fails_open(self):
        path = _connect_state._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")

        _forget_in_process_state()
        assert _mcp_discovery._connect_cooldown_active("broken") is False

    def test_scoped_connection_does_not_shadow_other_scope(self):
        _connect_state.stamp(("profile-a", "shared"), 1, time.time() + 300.0)

        assert _connect_state.deadline(("profile-b", "shared")) is None
        assert _connect_state.deadline(("profile-a", "shared")) is not None
