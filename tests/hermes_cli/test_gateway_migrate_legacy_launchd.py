"""``hermes gateway migrate --multiplex`` must sweep LaunchAgents the per-profile label scheme
can't map (#143390 — a legacy-named per-profile agent survived the fold's discovery, launchd
restarted its standalone gateway later, and it reclaimed host ownership from the multiplexer).

Ownership is judged from each plist's pinned ``HERMES_HOME`` (the boundary
``legacy_launchd_labels_for_install`` and the uninstall sweep already hold), never from label
shape, so a sandboxed or side-by-side install's fleet is never enumerated (#41403).
"""
from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

from hermes_cli import gateway as gw
from hermes_cli import gateway_migrate as gm


def _write_agent(agents: Path, label: str, pinned_home: Path) -> Path:
    agents.mkdir(parents=True, exist_ok=True)
    plist_path = agents / f"{label}.plist"
    plist_path.write_bytes(plistlib.dumps({
        "Label": label,
        "ProgramArguments": ["/bin/hermes", "gateway", "run"],
        "RunAtLoad": True,
        "EnvironmentVariables": {"HERMES_HOME": str(pinned_home)},
    }))
    return plist_path


def _fake_agents(tmp_path, monkeypatch):
    """An empty LaunchAgents dir standing in for the account's; platform probes pinned so the
    launchd branch runs on every CI OS, not just darwin."""
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    monkeypatch.setattr(gw, "get_launchd_plist_path", lambda: agents / "ai.hermes.gateway.plist")
    monkeypatch.setattr(gw, "is_macos", lambda: True)  # os-marker: ok — pure plist-path bookkeeping, no launchd call happens
    monkeypatch.setattr(gw, "supports_systemd_services", lambda: False)
    monkeypatch.setattr(gw, "is_windows", lambda: False)
    monkeypatch.setattr(gw, "_running_under_s6", lambda: False)
    return agents


def test_installed_services_counts_legacy_pinned_plist(tmp_path, monkeypatch):
    agents = _fake_agents(tmp_path, monkeypatch)
    profile_home = tmp_path / "hermes" / "profiles" / "passarim"
    profile_home.mkdir(parents=True)
    _write_agent(agents, "ai.hermes.gateway.passarim", profile_home)

    assert gm._installed_services(profile_home) == [("launchd", False)]


def test_installed_services_ignores_foreign_pinned_plist(tmp_path, monkeypatch):
    agents = _fake_agents(tmp_path, monkeypatch)
    profile_home = tmp_path / "hermes" / "profiles" / "passarim"
    profile_home.mkdir(parents=True)
    other_install = tmp_path / "side-by-side" / "profiles" / "passarim"
    other_install.mkdir(parents=True)
    _write_agent(agents, "ai.hermes.gateway.passarim", other_install)

    assert gm._installed_services(profile_home) == []


def test_bootout_legacy_launchd_boots_both_domains_then_unlinks(tmp_path, monkeypatch):
    agents = _fake_agents(tmp_path, monkeypatch)
    profile_home = tmp_path / "hermes" / "profiles" / "passarim"
    profile_home.mkdir(parents=True)
    legacy = _write_agent(agents, "ai.hermes.gateway.passarim", profile_home)
    # A foreign-pinned agent with a gateway-shaped label must survive the sweep.
    foreign = _write_agent(agents, "io.nousresearch.hermes-agent.gateway", tmp_path / "elsewhere")

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert gm._bootout_legacy_launchd(profile_home) == ["ai.hermes.gateway.passarim"]
    import os
    uid = os.getuid()
    assert calls == [
        ["launchctl", "bootout", f"gui/{uid}/ai.hermes.gateway.passarim"],
        ["launchctl", "bootout", f"user/{uid}/ai.hermes.gateway.passarim"],
    ]
    assert not legacy.exists() and foreign.exists()


def test_bootout_legacy_launchd_without_agents_dir(tmp_path, monkeypatch):
    agents = _fake_agents(tmp_path, monkeypatch)
    agents.rmdir()  # the LaunchAgents dir itself is gone (fresh account, manual cleanup)
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kwargs: (_ for _ in ()).throw(AssertionError("no launchctl expected")))

    assert gm._bootout_legacy_launchd(tmp_path / "hermes" / "profiles" / "nobody") == []


def test_remove_secondary_gateways_sweeps_legacy_agents(tmp_path, monkeypatch, capsys):
    agents = _fake_agents(tmp_path, monkeypatch)
    profile_home = tmp_path / "hermes" / "profiles" / "passarim"
    profile_home.mkdir(parents=True)
    legacy = _write_agent(agents, "ai.hermes.gateway.passarim", profile_home)
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0))

    plan = gm.MigrationPlan(
        default_home=tmp_path / "hermes", profiles=[
            gm.ProfileGateway(name="default", home=tmp_path / "hermes"),
            gm.ProfileGateway(name="passarim", home=profile_home, pid=4101),
        ],
        multiplex_flag_on=False, live_served=None,
    )
    monkeypatch.setattr(gm, "_stop_gateway_process", lambda home: None)

    gm._remove_secondary_gateways(plan)

    assert not legacy.exists()
    assert "removed legacy LaunchAgent ai.hermes.gateway.passarim" in capsys.readouterr().out
