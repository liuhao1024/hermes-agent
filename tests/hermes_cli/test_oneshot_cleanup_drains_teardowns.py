"""One-shot runs (``hermes chat -q`` / ``hermes -z``) leave through ``os._exit``,
which skips atexit — the pre-exit cleanup must run terminal_tool's drain
variant so sandbox teardown workers finish their ``docker stop``/``docker rm``
before the hard exit, instead of dying mid-remove and leaving stopped
containers behind (#141661)."""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh_oneshot_cleanup(monkeypatch):
    """Allow exactly one ``_cleanup_oneshot_runtime()`` run inside this test."""
    from hermes_cli import main as hermes_main

    monkeypatch.setattr(hermes_main, "_oneshot_cleanup_done", False)
    return hermes_main._cleanup_oneshot_runtime


def test_oneshot_cleanup_runs_terminal_tools_atexit_drain(
    fresh_oneshot_cleanup, monkeypatch
):
    """The one-shot cleanup table must call the drain variant, not bare
    ``cleanup_all_environments``: ``os._exit`` kills daemon teardown threads
    mid-``docker rm`` otherwise."""
    from tools import terminal_tool

    calls: list[str] = []
    monkeypatch.setattr(terminal_tool, "_atexit_cleanup", lambda: calls.append("drain"))
    monkeypatch.setattr(
        terminal_tool, "cleanup_all_environments", lambda: calls.append("bare")
    )

    fresh_oneshot_cleanup()

    assert calls == ["drain"]


def test_oneshot_cleanup_waits_for_sandbox_teardown_workers(
    fresh_oneshot_cleanup, monkeypatch
):
    """A sandbox's teardown worker must be joined before the hard exit, and so
    must workers of envs the idle reaper already detached from the registry
    (#86317) — those handles are only reachable through the teardown drain."""
    import tools.environments.docker as docker_mod
    from tools import terminal_tool

    waited = {"env": False, "detached": False}

    class _FakeEnv:
        def wait_for_cleanup(self, timeout: float = 15.0) -> bool:
            waited["env"] = True
            return True

    def _drain(timeout: float = 15.0) -> bool:
        waited["detached"] = True
        return True

    monkeypatch.setattr(terminal_tool, "cleanup_all_environments", lambda: 0)
    monkeypatch.setitem(terminal_tool._active_environments, "task-drain-1", _FakeEnv())
    monkeypatch.setattr(
        docker_mod.DockerEnvironment, "wait_for_all_teardowns", staticmethod(_drain)
    )

    fresh_oneshot_cleanup()

    assert waited == {"env": True, "detached": True}
