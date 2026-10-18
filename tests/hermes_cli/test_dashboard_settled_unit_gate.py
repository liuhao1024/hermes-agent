"""A unit the update fleet loop already settled must not be restarted a second time by the
dashboard cleanup pass (#141021).

``hermes update``'s systemd fleet-restart loop declines a system-scope ``hermes-dashboard``
when no non-interactive privilege path exists (no passwordless sudo, no manage-units polkit
authorization) and prints its own "restart it manually" instruction. The post-update dashboard
cleanup then ran ``_restart_managed_dashboard_service`` unconditionally: the plain ``systemctl
restart`` died in polkit's textual authentication agent (no controlling terminal inside the
captured subprocess) and ``sudo -n`` failed on the password prompt, printing a second failure
dump and a duplicate "restart manually" block for a unit the fleet loop had already settled.
"""

from __future__ import annotations

import pytest

from hermes_cli import dashboard_procs


@pytest.fixture
def restart_calls(monkeypatch):
    """Count ``_restart_managed_dashboard_service`` invocations; no stale PIDs exist so the
    scan settles at the empty result either way."""
    calls: list[str] = []
    monkeypatch.setattr(
        "hermes_cli.main_dashboard._restart_managed_dashboard_service",
        lambda reason, unit="hermes-dashboard.service": calls.append(reason) or True,
    )
    monkeypatch.setattr(
        "hermes_cli.main_dashboard._find_stale_dashboard_pids",
        lambda exclude_pids=None, scope_home=None: [],
    )
    monkeypatch.setattr(dashboard_procs, "_exclude_pids_from_env", lambda: set())
    monkeypatch.setattr(dashboard_procs, "_lock_owned_serve_pids", lambda: set())
    return calls


def test_settled_dashboard_unit_is_not_retried(restart_calls):
    result = dashboard_procs._kill_stale_dashboard_processes(
        restart_managed=True, already_restarted_units={"hermes-dashboard"})
    assert restart_calls == []
    assert result["matched"] == []


def test_unset_unit_still_restarts_the_managed_service(restart_calls):
    dashboard_procs._kill_stale_dashboard_processes(restart_managed=True)
    assert len(restart_calls) == 1


def test_other_settled_units_do_not_gate_the_dashboard_restart(restart_calls):
    dashboard_procs._kill_stale_dashboard_processes(
        restart_managed=True, already_restarted_units={"hermes-serve"})
    assert len(restart_calls) == 1
