"""The adopt path must keep the idle-unload contract after the booter dies.

``_start_idle_sweeper`` runs only for the process that booted the router, and its
daemon thread dies with that process; the detached router then keeps idle models
resident forever. A process adopting the running server has to sweep it too —
without ever owning it, since adopters must not stop another's server at exit."""

from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def _retire_sweepers(monkeypatch):
    """Fail-safe teardown: any sweeper a test left running must observe "no
    state" and retire BEFORE this test's patches come off — afterwards the loop
    would re-import the real endpoint reader and probe this machine's actual
    runtimes dir."""
    yield
    import hermes_cli.local_runtime.bootstrap as boot

    if getattr(boot, "_ADOPTED_SWEEPING", False):
        monkeypatch.setattr(
            "hermes_cli.local_runtime.endpoint._state_endpoint", lambda: None
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and boot._ADOPTED_SWEEPING:
            time.sleep(0.02)


def _stage(home, name):
    mdir = home / "models"
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / f"{name}.gguf").write_bytes(b"GGUF" + b"\x00" * 32)


def _write_presets(home, *model_ids):
    pdir = home / "runtimes" / "llamacpp"
    pdir.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        f"[{m}]\nmodel = {home / 'models' / (m + '.gguf')}\nctx-size = 65536\n"
        for m in model_ids
    )
    (pdir / "presets.ini").write_text(body, encoding="utf-8")


def _wire_fake_router(monkeypatch):
    """Patch the supervisor class with a recording fake. The bootstrap builds
    shells via ``LlamaServerSupervisor.__new__``, so a plain class whose
    instances carry ``sweep_idle`` stands in exactly for the real one."""
    import hermes_cli.local_runtime.supervisor as sup_mod

    record = {"swept_by": []}

    class _Shell:
        def sweep_idle(self, now=None):
            record["swept_by"].append(self)
            self._idle_since.setdefault("model-a", 42.0)
            return []

    monkeypatch.setattr(sup_mod, "LlamaServerSupervisor", _Shell)
    return record


def _wait_for(predicate, timeout_s: float = 5.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _adopt(hermes_home, monkeypatch, state_box):
    """Drive ensure_local_runtime down the adopt branch (state file present,
    presets current) and hand back the fake-router record."""
    import hermes_cli.local_runtime.bootstrap as boot

    _stage(hermes_home, "model-a")
    _write_presets(hermes_home, "model-a")
    monkeypatch.setattr(boot, "_SUPERVISOR", None)
    monkeypatch.setattr(boot, "_SWEEP_EVERY_S", 0.05)
    monkeypatch.setattr(
        "hermes_cli.local_runtime.endpoint._state_endpoint", lambda: state_box["state"]
    )
    record = _wire_fake_router(monkeypatch)

    assert boot.ensure_local_runtime({"local_runtime": {"enabled": True}}) is None
    return boot, record


def test_adopted_server_is_swept_but_never_owned(hermes_home, monkeypatch):
    boot, record = _adopt(
        hermes_home,
        monkeypatch,
        {"state": {"base_url": "http://127.0.0.1:18434/v1", "api_key": "k"}},
    )

    assert boot._SUPERVISOR is None, "an adopter must not own the adopted server"
    assert _wait_for(lambda: len(record["swept_by"]) >= 2), (
        "adopted server was never swept"
    )
    # One shell across ticks: the idle clocks survive between sweeps.
    assert all(s is record["swept_by"][0] for s in record["swept_by"])
    assert record["swept_by"][0].port == 18434

    boot._SUPERVISOR = object()
    assert _wait_for(lambda: not boot._ADOPTED_SWEEPING), "sweeper never retired"
    boot._SUPERVISOR = None


def test_sweeper_stops_when_the_state_file_disappears(hermes_home, monkeypatch):
    box = {"state": {"base_url": "http://127.0.0.1:18434/v1", "api_key": "k"}}
    boot, record = _adopt(hermes_home, monkeypatch, box)

    assert _wait_for(lambda: record["swept_by"]), "adopted server was never swept"
    sweeps = len(record["swept_by"])
    box["state"] = None  # the router died or was replaced; the guard now reads nothing

    assert _wait_for(lambda: not boot._ADOPTED_SWEEPING), "sweeper outlived the router"
    assert len(record["swept_by"]) == sweeps, (
        "sweeper ran after the state file was gone"
    )


def test_sweeper_yields_to_a_supervisor_booted_in_this_process(
    hermes_home, monkeypatch
):
    box = {"state": {"base_url": "http://127.0.0.1:18434/v1", "api_key": "k"}}
    boot, record = _adopt(hermes_home, monkeypatch, box)

    assert _wait_for(lambda: record["swept_by"]), "adopted server was never swept"
    boot._SUPERVISOR = (
        object()
    )  # this process took over; the spawn-path sweeper runs now

    assert _wait_for(lambda: not boot._ADOPTED_SWEEPING), "sweeper never yielded"
    assert box["state"] is not None, (
        "retirement must be due to ownership, not a lost state file"
    )
    boot._SUPERVISOR = None


def test_repeated_ensure_does_not_stack_sweeper_loops(hermes_home, monkeypatch):
    boot, _ = _adopt(
        hermes_home,
        monkeypatch,
        {"state": {"base_url": "http://127.0.0.1:18434/v1", "api_key": "k"}},
    )
    assert boot.ensure_local_runtime({"local_runtime": {"enabled": True}}) is None

    loops = [
        t for t in threading.enumerate() if t.name == "local-runtime-adopted-idle-sweep"
    ]
    assert len(loops) == 1, f"expected one adopted sweeper loop, found {len(loops)}"


def test_retarget_keeps_the_idle_clocks_across_a_router_restart(
    hermes_home, monkeypatch
):
    box = {"state": {"base_url": "http://127.0.0.1:18434/v1", "api_key": "k"}}
    _, record = _adopt(hermes_home, monkeypatch, box)

    assert _wait_for(lambda: record["swept_by"]), "adopted server was never swept"
    first = record["swept_by"][0]
    assert first._idle_since == {"model-a": 42.0}

    box["state"] = {"base_url": "http://127.0.0.1:18435/v1", "api_key": "k2"}
    assert _wait_for(lambda: record["swept_by"][-1] is not first), (
        "shell was not re-targeted"
    )
    retargeted = record["swept_by"][-1]
    assert retargeted.port == 18435
    assert retargeted._idle_since == {"model-a": 42.0}, (
        "idle clocks were dropped on re-target"
    )
