"""A Windows worker imports numpy before the managed-controls reader parks on stdin (#142271)."""

import builtins
import inspect
import sys

import pytest

from agent import managed_worker


def _tracking_import(monkeypatch, seen, error=None):
    real_import = builtins.__import__

    def recorder(name, *args, **kwargs):
        if name == "numpy":
            seen.append(name)
            if error is not None:
                raise error
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", recorder)


def test_windows_imports_numpy_before_the_controls_reader_thread(monkeypatch):
    seen = []
    _tracking_import(monkeypatch, seen)
    monkeypatch.setattr(sys, "platform", "win32")
    managed_worker._preimport_windows_numpy()
    # A cold interpreter importing numpy runs many `import numpy` statements inside
    # numpy's own submodules, so only the presence and first position are stable.
    assert seen[:1] == ["numpy"]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the POSIX arm of the gate; the fake-win32 test covers Windows",
)
def test_posix_keeps_the_lazy_import(monkeypatch):
    seen = []
    _tracking_import(monkeypatch, seen)
    managed_worker._preimport_windows_numpy()
    assert seen == []


def test_missing_numpy_stays_the_provider_degradation_path(monkeypatch):
    seen = []
    _tracking_import(
        monkeypatch, seen, error=ImportError("no numpy in this interpreter")
    )
    monkeypatch.setattr(sys, "platform", "win32")
    managed_worker._preimport_windows_numpy()  # the guard itself must never fail the worker
    assert seen == ["numpy"]


def test_execute_runs_the_guard_before_starting_the_controls_reader():
    """The guard only holds while no stdin reader thread exists yet (execute's ordering)."""
    source = inspect.getsource(managed_worker.execute)
    assert source.index("_preimport_windows_numpy()") < source.index(
        "controls = WorkerControls("
    )
