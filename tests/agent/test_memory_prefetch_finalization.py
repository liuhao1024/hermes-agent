"""Real spill finalization must retain the shared gate until local publication finishes."""

import threading
from types import SimpleNamespace

import pytest

import agent.memory_manager as mm
from agent.memory_provider import MemoryProvider
from tools import spill_safety

A = "What was the earlier database decision?"
B = "What is the current deployment plan?"
C = "Which release checks should run first?"


class _CompletionError(BaseException):
    """Escape the spill transport's ordinary I/O-error fallback, through real complete()."""


class _Provider(MemoryProvider):
    name = "spill-finalization"

    def __init__(self, value):
        self.value = value
        self.started = threading.Event()
        self.release = threading.Event()
        self.workers = []
        self.calls = []

    def is_available(self):
        return True

    def initialize(self, session_id, **kwargs):
        pass

    def get_tool_schemas(self):
        return []

    def prefetch(self, query, *, session_id=""):
        self.workers.append(threading.current_thread())
        self.calls.append((query, session_id))
        self.started.set()
        assert self.release.wait(10), "fixture did not release the provider"
        return self.value


@pytest.fixture
def finalization(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setattr(mm, "_MAX_INFLIGHT_CALLS", 1)
    managers, providers, errors = [], [], []
    spill_started, release_spill = threading.Event(), threading.Event()
    monkeypatch.setattr(threading, "excepthook", errors.append)

    def make(value):
        manager = mm.memory_manager_from_config({
            "external_prefetch_timeout": 0.01, "defer_prefetch_on_timeout": True,
        })
        provider = _Provider(value)
        manager.add_provider(provider)
        manager._external_prefetch_spill_config = {
            "enabled": True, "max_chars": 1000, "preview_head": 10, "preview_tail": 10,
            "directory": str(tmp_path / "spill"),
        }
        managers.append(manager)
        providers.append(provider)
        return manager, provider

    yield SimpleNamespace(make=make, spill_started=spill_started, release_spill=release_spill,
                          errors=errors, spill_dir=tmp_path / "spill")
    release_spill.set()
    for provider in providers:
        provider.release.set()
    for provider in providers:
        for worker in provider.workers:
            worker.join(10)
            assert not worker.is_alive()
    for manager in managers:
        manager.shutdown_all()


@pytest.mark.parametrize("outcome", ["saved", "io-error", "completion-error"])
def test_spill_worker_owns_capacity_through_publication_and_exception_cleanup(finalization, monkeypatch, outcome):
    h = finalization
    raw = "EARLIER-FACT\n" * 200
    first, provider = h.make(raw)
    second, other = h.make("READY-OTHER")
    # Seed real ready recall before another manager occupies this same home/provider's cap.
    assert second.prefetch_all(A, session_id="other") == ""
    assert other.started.wait(10)
    other.release.set()
    other.workers[0].join(10)
    assert not other.workers[0].is_alive()
    other.value = "FRESH-OTHER"
    actual_write = spill_safety.write_text_exclusive

    def held_write(path, text, **kwargs):
        h.spill_started.set()
        assert h.release_spill.wait(10), "fixture did not release the spill transport"
        if outcome == "io-error":
            raise OSError("spill transport failed")
        if outcome == "completion-error":
            raise _CompletionError("completion spill aborted")
        return actual_write(path, text, **kwargs)

    monkeypatch.setattr(spill_safety, "write_text_exclusive", held_write)
    assert first.prefetch_all(A, session_id="first") == ""
    assert provider.started.wait(10)
    provider.release.set()
    assert h.spill_started.wait(10)
    worker = provider.workers[0]
    assert worker.is_alive()
    key = first._registry_key(provider)
    assert key == second._registry_key(other)
    gate = first._PREFETCH_GATE
    with first._late_prefetch_lock:
        call = first._deferred_prefetch._active[provider.name]
        assert call.thread is worker and not call.done and not call.waiting
        assert provider.name not in first._late_prefetch
    with gate.lock:
        registered = worker in gate.calls.get(key, {})
    # Gate denial must not swallow an already-ready value or strand its local lease.
    ready = second.prefetch_all(B, session_id="other")
    assert "READY-OTHER" in ready and "earlier-turn recall" in ready.lower()
    assert registered, "shared gate released a live worker blocked in real completion spill"
    assert other.calls == [(A, "other")], "capacity one admitted another worker during spill"
    assert second.prefetch_all(C, session_id="other") == ""
    with second._late_prefetch_lock:
        assert not second._deferred_prefetch._active and not second._late_prefetch
    with gate.lock:
        assert set(gate.calls[key]) == {worker}
    h.release_spill.set()
    worker.join(10)
    assert not worker.is_alive()
    assert call.done and not first._deferred_prefetch._active
    with gate.lock:
        assert all(key not in state for state in (gate.calls, gate.last_ok, gate.probe_at, gate.skips))
    if outcome == "completion-error":
        assert len(h.errors) == 1 and isinstance(h.errors[0].exc_value, _CompletionError)
        assert h.errors[0].thread is worker
        assert call.value == "" and not first._late_prefetch
    else:
        assert h.errors == []
        assert "output truncated" in call.value
        assert first._late_prefetch[provider.name].value == call.value
        if outcome == "saved":
            spills = list((h.spill_dir / "first").glob("*.txt"))
            assert len(spills) == 1 and spills[0].read_text(encoding="utf-8") == raw
            assert str(spills[0]) in call.value
        else:
            assert "spill write failed" in call.value
            assert list(h.spill_dir.rglob("*.txt")) == []
    # Both the shared capacity and local ownership recover, with ready recall consumed once.
    first._external_prefetch_timeout = second._external_prefetch_timeout = 5
    provider.value = ""
    carried = first.prefetch_all(B, session_id="first")
    if outcome == "completion-error":
        assert carried == ""
    else:
        assert call.value in carried and "earlier-turn recall" in carried.lower()
    assert first.prefetch_all(C, session_id="first") == ""
    assert second.prefetch_all(C, session_id="other") == "FRESH-OTHER"
    assert other.calls == [(A, "other"), (C, "other")]
