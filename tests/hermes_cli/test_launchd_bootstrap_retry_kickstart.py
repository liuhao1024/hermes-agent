"""`_retry_launchctl_bootstrap_until_registered` must kickstart a registered-but-not-running label.

launchd does not start an on-demand job (no RunAtLoad/KeepAlive) at `bootstrap` in a per-user
`gui/<uid>` domain, so the retry loop used to re-bootstrap a label it had just registered: that
only EIOs (rc=5) into a bootout and never starts a process until the deadline (#142741).
"""

import subprocess
from types import SimpleNamespace

import hermes_cli.gateway as gateway_cli
from hermes_cli import gateway_launchd

LABEL = "ai.hermes.gateway"
DOMAIN = "gui/501"


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _retry_state(monkeypatch):
    state = SimpleNamespace(
        supervised=False,
        bootstraps=[],
        kickstarts=[],
        logs=[],
    )
    monkeypatch.setattr(
        gateway_cli,
        "_launchctl_label_supervising_process",
        lambda label: state.supervised,
    )
    monkeypatch.setattr(
        gateway_cli,
        "_launchctl_bootstrap",
        lambda domain, plist_path, label, timeout=30: state.bootstraps.append(
            (domain, label)
        ),
    )
    monkeypatch.setattr(
        gateway_cli,
        "_launchd_kickstart",
        lambda label, domain: state.kickstarts.append((label, domain)),
    )
    monkeypatch.setattr(
        gateway_cli,
        "_append_launchd_reload_log",
        lambda message: state.logs.append(message),
    )
    clock = _FakeClock()
    monkeypatch.setattr(gateway_launchd, "time", clock)
    return state, clock


def test_on_demand_label_is_kickstarted_after_bootstrap_registers_it(
    tmp_path, monkeypatch
):
    state, clock = _retry_state(monkeypatch)

    def _kickstart(label, domain):
        state.kickstarts.append((label, domain))
        state.supervised = True

    monkeypatch.setattr(gateway_cli, "_launchd_kickstart", _kickstart)

    assert gateway_launchd._retry_launchctl_bootstrap_until_registered(
        DOMAIN, tmp_path / "plist", LABEL, deadline=clock.now + 60
    )
    assert state.bootstraps == [
        (DOMAIN, LABEL)
    ]  # bootstrap registers the label exactly once
    assert state.kickstarts == [
        (LABEL, DOMAIN)
    ]  # the start itself comes from kickstart
    assert state.logs == [
        "bootstrap attempt 1 exited 0 but gui/501/ai.hermes.gateway has "
        "no supervised process (launchctl list) — retrying"
    ]


def test_kickstart_failure_reloads_the_plist_next_round(tmp_path, monkeypatch):
    state, clock = _retry_state(monkeypatch)

    def _kickstart(label, domain):
        state.kickstarts.append((label, domain))
        raise subprocess.CalledProcessError(5, ["launchctl", "kickstart"])

    def _bootstrap(domain, plist_path, label, timeout=30):
        state.bootstraps.append((domain, label))
        if len(state.bootstraps) == 2:
            state.supervised = True  # the reloaded definition starts cleanly

    monkeypatch.setattr(gateway_cli, "_launchd_kickstart", _kickstart)
    monkeypatch.setattr(gateway_cli, "_launchctl_bootstrap", _bootstrap)

    assert gateway_launchd._retry_launchctl_bootstrap_until_registered(
        DOMAIN, tmp_path / "plist", LABEL, deadline=clock.now + 60
    )
    assert [log.split(" attempt")[0] for log in state.logs] == [
        "bootstrap",
        "kickstart",
    ]
    assert state.bootstraps == [(DOMAIN, LABEL), (DOMAIN, LABEL)]


def test_deadline_exhaustion_returns_false_without_bootstrap_churn(
    tmp_path, monkeypatch
):
    state, clock = _retry_state(
        monkeypatch
    )  # neither bootstrap nor kickstart ever yields a PID

    assert not gateway_launchd._retry_launchctl_bootstrap_until_registered(
        DOMAIN, tmp_path / "plist", LABEL, deadline=clock.now + 3
    )
    assert state.bootstraps == [
        (DOMAIN, LABEL)
    ]  # a registered label is never re-bootstrapped
    assert len(state.kickstarts) >= 2
    assert state.logs[-1].startswith("kickstart attempt")


def test_bootstrap_failure_never_kickstarts_an_unregistered_label(
    tmp_path, monkeypatch
):
    state, clock = _retry_state(monkeypatch)

    def _bootstrap(domain, plist_path, label, timeout=30):
        state.bootstraps.append((domain, label))
        raise subprocess.CalledProcessError(5, ["launchctl", "bootstrap"])

    monkeypatch.setattr(gateway_cli, "_launchctl_bootstrap", _bootstrap)

    assert not gateway_launchd._retry_launchctl_bootstrap_until_registered(
        DOMAIN, tmp_path / "plist", LABEL, deadline=clock.now + 3
    )
    assert len(state.bootstraps) >= 2
    assert state.kickstarts == []
