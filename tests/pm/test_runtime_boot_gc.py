"""A real bootstrap reader pins its generation before GC can select victims."""
import json
import os
import subprocess
import sys
import time


def _make_generation(state, name, *, mtime_age_seconds=None):
    from pm.environments import site_packages

    venv = state / "environments" / name / "venv"
    venv.mkdir(parents=True)
    # pyvenv.cfg first: the layout keys its site-packages path off the recorded version.
    (venv / "pyvenv.cfg").write_text("version = 3.11")
    site_packages(venv).mkdir(parents=True)
    (venv.parent / ".lease-managed").touch()
    if mtime_age_seconds is not None:
        stamp = time.time() - mtime_age_seconds
        os.utime(venv.parent / ".lease-managed", (stamp, stamp))
    return venv.parent


def test_bootstrap_lease_survives_selection_change(tmp_path, monkeypatch):
    from pm.environments import install_state_dir, runtime_facts_path, site_packages
    from hermes_cli.runtime_state import collect_generations

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    state = install_state_dir(repo)
    for name in ("first", "second", "unused"):
        venv = state / "environments" / name / "venv"
        venv.mkdir(parents=True)
        # pyvenv.cfg first: the layout keys its site-packages path off the recorded version.
        (venv / "pyvenv.cfg").write_text("version = 3.11")
        site_packages(venv).mkdir(parents=True)
        (venv.parent / ".lease-managed").touch()
    legacy = state / "environments" / "old-unleased"
    legacy.mkdir()

    def select(name):
        environment = state / "environments" / name / "venv"
        runtime_facts_path(repo).write_text(json.dumps({"packages": {"venv": {"environment": str(environment)}}}))
        return environment

    first = select("first")
    code = '''
import sys
from pathlib import Path
from pm.environments import activate_dependencies
activate_dependencies(Path(sys.argv[1]))
print("ready", flush=True)
sys.stdin.readline()
'''
    child = subprocess.Popen([sys.executable, "-c", code, str(repo)], env=dict(os.environ),
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        select("second")
        collect_generations(repo, min_age_seconds=0)
        assert first.is_dir()
        assert not (state / "environments" / "unused").exists()
        assert legacy.is_dir()
    finally:
        child.communicate("done\n", timeout=15)
    assert child.returncode == 0
    collect_generations(repo, min_age_seconds=0)
    assert not first.exists()
    assert (state / "environments" / "second").is_dir()
    assert legacy.is_dir()


_BOOT_CODE = """
import sys
from pathlib import Path
from pm.environments import activate_dependencies
activate_dependencies(Path(sys.argv[1]))
print("ready", flush=True)
"""


def test_boot_sweep_collects_expired_orphan(tmp_path, monkeypatch):
    """The boot path itself reclaims unselected generations past the retention window (#145958).

    A long-lived multi-profile host only rotates selections; nothing there runs a dependency
    sync or `hermes pm gc`, so this boot-time sweep is the only automatic collector it sees.
    """
    from pm.environments import install_state_dir, runtime_facts_path

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    state = install_state_dir(repo)
    _make_generation(state, "first")
    _make_generation(state, "fresh")
    _make_generation(state, "stale-orphan", mtime_age_seconds=2 * 86400)

    environment = state / "environments" / "first" / "venv"
    runtime_facts_path(repo).write_text(
        json.dumps({"packages": {"venv": {"environment": str(environment)}}})
    )

    result = subprocess.run(
        [sys.executable, "-c", _BOOT_CODE, str(repo)],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert not (state / "environments" / "stale-orphan").exists(), (
        "the boot sweep must collect the expired unleased orphan"
    )
    assert (state / "environments" / "first").is_dir(), "the selected generation must survive"
    assert (state / "environments" / "fresh").is_dir(), (
        "a generation inside the retention window must survive")


def test_boot_sweep_respects_live_lease(tmp_path, monkeypatch):
    """A generation whose lease some process still holds survives boot sweeps; once the holder
    exits, the next boot reclaims it."""
    from hermes_cli.runtime_state import lease_directory
    from pm.environments import install_state_dir, runtime_facts_path

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    state = install_state_dir(repo)
    _make_generation(state, "first")
    stale = _make_generation(state, "stale-leased", mtime_age_seconds=2 * 86400)

    environment = state / "environments" / "first" / "venv"
    runtime_facts_path(repo).write_text(
        json.dumps({"packages": {"venv": {"environment": str(environment)}}})
    )

    release = lease_directory(stale)
    try:
        result = subprocess.run(
            [sys.executable, "-c", _BOOT_CODE, str(repo)],
            env=dict(os.environ),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert stale.is_dir(), "a live lease must protect its generation from the boot sweep"
    finally:
        release()
    result = subprocess.run(
        [sys.executable, "-c", _BOOT_CODE, str(repo)],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert not stale.exists(), "the next boot after the lease holder exits must reclaim it"


def test_collect_zero_timeout_skips_held_install_lock(tmp_path, monkeypatch):
    """lock_timeout=0 makes the sweep non-blocking: a held install lock skips it silently,
    so a boot-time caller never queues behind another process's dependency rebuild."""
    from hermes_cli.runtime_state import collect_generations, runtime_lock
    from pm.environments import install_state_dir

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    state = install_state_dir(repo)
    stale = _make_generation(state, "stale-orphan", mtime_age_seconds=2 * 86400)

    # flock semantics make a second descriptor in this very process a real competing holder,
    # so this exercises the same skip path an unrelated rebuild would trigger.
    with runtime_lock(repo):
        assert collect_generations(repo, lock_timeout=0) == []
    assert stale.is_dir(), "a held install lock must skip the sweep, not collect under it"
    assert collect_generations(repo, lock_timeout=0) == [stale]
