"""Opt-in earlier-turn recall through the real turn seam, without external I/O."""

import asyncio
import contextvars
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.memory_manager as mm
from agent.memory_provider import MemoryProvider
from agent.turn_context import _memory_turn_start_and_prefetch, _stamp_api_content_sidecar
from hermes_cli.config import load_config
from hermes_constants import reset_hermes_home_override, set_hermes_home_override

A = "What did we decide about the database migration?"
B = "How should we organize the release checklist?"
C = "Which deployment checks should run first?"


@dataclass
class Recall:
    value: str = ""
    error: Exception | None = None
    blocked: bool = False
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)


class Provider(MemoryProvider):
    name = "deferred-test"

    def __init__(self, plans):
        self.plans = plans
        self.calls = []
        self.contexts = []

    def is_available(self):
        return True

    def initialize(self, session_id, **kwargs):
        pass

    def get_tool_schemas(self):
        return []

    def prefetch(self, query, *, session_id=""):
        self.calls.append((query, session_id))
        self.contexts.append(probe.get())
        plan = self.plans.get(query, Recall())
        plan.started.set()
        if plan.blocked:
            assert plan.release.wait(5), "test failed to release the real worker"
        if plan.error:
            raise plan.error
        return plan.value


probe = contextvars.ContextVar("deferred-prefetch-test", default="missing")


@pytest.fixture
def harness(monkeypatch, tmp_path):
    # Both HOME and HERMES_HOME are isolated, including config/spill writes.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    threads, providers, managers = [], [], []
    spawn = mm.spawn_context_thread

    def record(*args, **kwargs):
        thread = spawn(*args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(mm, "spawn_context_thread", record)

    def make(plans, enabled=True):
        config = {"external_prefetch_timeout": 0.01}
        if enabled is not None:
            config["defer_prefetch_on_timeout"] = enabled
        manager = mm.memory_manager_from_config(config)
        provider = Provider(plans)
        providers.append(provider)
        managers.append(manager)
        manager.add_provider(provider)
        return manager, provider

    yield SimpleNamespace(make=make, threads=threads, home=tmp_path / "hermes")
    for provider in providers:
        for plan in provider.plans.values():
            plan.release.set()
    for thread in threads:
        if thread.ident is not None:
            threading.Thread.join(thread, 5)
            assert not thread.is_alive()
    for manager in managers:
        manager.shutdown_all()


def finish(harness, recall):
    assert recall.started.wait(5)
    recall.release.set()
    # Joining the actual gated worker also proves its cache publication completed.
    for thread in list(harness.threads):
        if thread.ident is not None:
            threading.Thread.join(thread, 5)
            assert not thread.is_alive()


def ready_a(harness, *, enabled=True, value="EARLIER-A", error=None, session="session"):
    late = Recall(value, error=error, blocked=True)
    manager, provider = harness.make({A: late}, enabled=enabled)
    assert manager.prefetch_all(A, session_id=session) == ""
    finish(harness, late)
    manager._external_prefetch_timeout = 5
    return manager, provider


@pytest.mark.parametrize("enabled", [None, False])
def test_default_keeps_exact_query_replay_without_fresh_lookup(harness, enabled):
    manager, provider = ready_a(harness, enabled=enabled)
    assert manager.prefetch_all(A, session_id="session") == "EARLIER-A"
    assert provider.calls == [(A, "session")]
    provider.plans[B] = Recall("FRESH-B")
    assert manager.prefetch_all(B, session_id="session") == "FRESH-B"


def test_carry_once_via_turn_seam_and_current_user_sidecar_with_fresh_b(harness):
    late = Recall("EARLIER-A", blocked=True)
    manager, provider = harness.make({A: late, B: Recall("FRESH-B")})
    agent = SimpleNamespace(_memory_manager=manager, _user_turn_count=1, session_id="session")
    assert _memory_turn_start_and_prefetch(agent, A) == ""
    finish(harness, late)
    manager._external_prefetch_timeout = 5
    # Trivial turns do not claim the pending value.
    assert _memory_turn_start_and_prefetch(agent, "thanks") == ""
    history = [{"role": "system", "content": "STATIC"}, {"role": "user", "content": A},
               {"role": "assistant", "content": "reply A"}]
    original = deepcopy(history)
    messages = history + [{"role": "user", "content": B}]
    context = _memory_turn_start_and_prefetch(agent, B)
    _stamp_api_content_sidecar(agent, messages, 3, context, "", preflight_compressed=False)
    wire = messages[-1]["api_content"]
    assert "EARLIER-A" in wire and "FRESH-B" in wire
    assert "earlier-turn recall" in wire.lower() and "previous question" in wire.lower()
    assert messages[:3] == original and messages[-1]["content"] == B
    assert provider.calls == [(A, "session"), (B, "session")]
    assert "EARLIER-A" not in _memory_turn_start_and_prefetch(agent, C)


@pytest.mark.parametrize("fresh", ["timeout", "error", "empty"])
def test_ready_a_survives_unsuccessful_fresh_b(harness, fresh):
    manager, provider = ready_a(harness)
    plan = Recall("LATE-B", blocked=fresh == "timeout", error=ValueError("unavailable") if fresh == "error" else None)
    if fresh == "empty":
        plan.value = ""
    provider.plans[B] = plan
    manager._external_prefetch_timeout = 0.01 if plan.blocked else 5
    result = manager.prefetch_all(B, session_id="session")
    assert "EARLIER-A" in result and "earlier-turn recall" in result.lower()
    assert provider.calls == [(A, "session"), (B, "session")]
    finish(harness, plan)
    manager._external_prefetch_timeout = 5
    following = manager.prefetch_all(C, session_id="session")
    assert "EARLIER-A" not in following
    assert ("LATE-B" in following) == plan.blocked


def test_running_lookup_fences_same_manager_but_not_other_managers(harness):
    late = Recall("EARLIER-A", blocked=True)
    manager, provider = harness.make({A: late})
    assert manager.prefetch_all(A, session_id="session") == ""
    assert late.started.wait(5)
    for query in (B, C, A):
        assert manager.prefetch_all(query, session_id="session") == ""
    assert provider.calls == [(A, "session")]
    other, other_provider = harness.make({B: Recall("OTHER-SESSION")})
    other._external_prefetch_timeout = 5
    assert other.prefetch_all(B, session_id="other") == "OTHER-SESSION"
    assert other_provider.calls == [(B, "other")]
    finish(harness, late)
    manager._external_prefetch_timeout = 5
    assert "EARLIER-A" in manager.prefetch_all(B, session_id="session")


BOUNDARIES = [
    ("switch", lambda m: m.on_session_switch("other")),
    ("reset", lambda m: m.on_session_switch("session", reset=True)),
    ("rewind", lambda m: m.on_session_switch("session", rewound=True)),
    ("compression", lambda m: m.on_pre_compress([])),
    ("end", lambda m: m.on_session_end([])),
    ("shutdown", lambda m: m.shutdown_all()),
    ("boundary", lambda m: m.commit_session_boundary_async([], new_session_id="other")),
]


@pytest.mark.parametrize("ready", [False, True], ids=["inflight", "ready"])
@pytest.mark.parametrize("name,boundary", BOUNDARIES, ids=[row[0] for row in BOUNDARIES])
def test_boundaries_invalidate_ready_and_inflight_completion(harness, ready, name, boundary):
    late = Recall("INVALID-A", blocked=True)
    manager, provider = harness.make({A: late, B: Recall("VALID-B")})
    assert manager.prefetch_all(A, session_id="session") == ""
    if ready:
        finish(harness, late)
    boundary(manager)
    assert manager.flush_pending(timeout=5)
    finish(harness, late)
    manager._external_prefetch_timeout = 5
    provider.plans[A] = Recall("NEW-A")
    assert "INVALID-A" not in manager.prefetch_all(A, session_id="session")
    assert "INVALID-A" not in manager.prefetch_all(B, session_id="session")
    if name == "shutdown":
        assert provider.calls == [(A, "session")]


@pytest.mark.parametrize("session", ["", "   "])
def test_empty_session_cannot_carry(harness, session):
    manager, _provider = ready_a(harness, session=session)
    assert "EARLIER-A" not in manager.prefetch_all(B, session_id=session)
    assert "EARLIER-A" not in manager.prefetch_all(B, session_id="session")


def test_implicit_session_change_fences_old_completion_even_on_return(harness):
    late = Recall("INVALID-A", blocked=True)
    manager, provider = harness.make({A: late, B: Recall("VALID-B")})
    assert manager.prefetch_all(A, session_id="old") == ""
    assert manager.prefetch_all(B, session_id="new") == ""
    assert provider.calls == [(A, "old")]
    finish(harness, late)
    manager._external_prefetch_timeout = 5
    assert "INVALID-A" not in manager.prefetch_all(B, session_id="old")
    assert "VALID-B" in manager.prefetch_all(B, session_id="new")


@pytest.mark.parametrize("ready", [False, True])
def test_profile_rebind_invalidates_same_session_recall(harness, tmp_path, ready):
    late = Recall("PROFILE-A", blocked=True)
    manager, provider = harness.make({A: late, B: Recall("PROFILE-B")})
    manager.initialize_all("session", hermes_home=str(harness.home))
    assert manager.prefetch_all(A, session_id="session") == ""
    if ready:
        finish(harness, late)
    home_b = tmp_path / "profile-b"
    token = set_hermes_home_override(home_b)
    try:
        manager.initialize_all("session", hermes_home=str(home_b))
        finish(harness, late)
        manager._external_prefetch_timeout = 5
        provider.plans[A] = Recall("PROFILE-B")
        assert "PROFILE-A" not in manager.prefetch_all(A, session_id="session")
        result = manager.prefetch_all(B, session_id="session")
        assert "PROFILE-A" not in result and "PROFILE-B" in result
    finally:
        reset_hermes_home_override(token)


@pytest.mark.parametrize("value,error", [("", None), ("   ", None), ("", ValueError("failed"))])
def test_empty_and_failed_late_results_are_discarded(harness, value, error):
    manager, provider = ready_a(harness, value=value, error=error)
    provider.plans[B] = Recall("FRESH-B")
    assert manager.prefetch_all(B, session_id="session") == "FRESH-B"


@pytest.mark.parametrize("failure", [KeyboardInterrupt, asyncio.CancelledError, RuntimeError])
def test_interrupted_join_propagates_cancellation_and_keeps_live_fence(harness, monkeypatch, failure):
    late = Recall("EARLIER-A", blocked=True)
    manager, provider = harness.make({A: late, B: Recall("FRESH-B")})
    spawn = mm.spawn_context_thread

    def interrupt_join(*args, **kwargs):
        thread = spawn(*args, **kwargs)

        def interrupted(timeout=None):
            assert late.started.wait(5)
            raise failure("injected wait interruption")

        monkeypatch.setattr(thread, "join", interrupted)
        return thread

    with monkeypatch.context() as patch:
        patch.setattr(mm, "spawn_context_thread", interrupt_join)
        if failure is RuntimeError:
            assert manager.prefetch_all(A, session_id="session") == ""
        else:
            with pytest.raises(failure):
                manager.prefetch_all(A, session_id="session")
    assert manager.prefetch_all(B, session_id="session") == ""
    assert provider.calls == [(A, "session")]
    finish(harness, late)
    manager._external_prefetch_timeout = 5
    assert "EARLIER-A" in manager.prefetch_all(B, session_id="session")
    assert "EARLIER-A" not in manager.prefetch_all(C, session_id="session")


def test_thread_start_failure_releases_local_slot_and_process_gate(harness, monkeypatch):
    manager, provider = harness.make({B: Recall("RECOVERED")})
    spawn = mm.spawn_context_thread

    def fail_start(*args, **kwargs):
        thread = spawn(*args, **kwargs)
        monkeypatch.setattr(thread, "start", lambda: (_ for _ in ()).throw(RuntimeError("cannot start")))
        return thread

    with monkeypatch.context() as patch:
        patch.setattr(mm, "spawn_context_thread", fail_start)
        assert manager.prefetch_all(A, session_id="session") == ""
    assert manager._registry_key(provider) not in manager._PREFETCH_GATE.calls
    manager._external_prefetch_timeout = 5
    assert manager.prefetch_all(B, session_id="session") == "RECOVERED"


def test_pending_value_claim_is_atomic_across_callers(harness):
    manager, provider = ready_a(harness)
    fresh = Recall("FRESH-B", blocked=True)
    provider.plans[B] = fresh
    start = threading.Barrier(3)
    outputs = []
    returned = threading.Event()

    def recall():
        start.wait(5)
        outputs.append(manager.prefetch_all(B, session_id="session"))
        returned.set()

    callers = [threading.Thread(target=recall) for _ in range(2)]
    for caller in callers:
        caller.start()
    try:
        start.wait(5)
        assert fresh.started.wait(5)
        # The second caller finishes while the first owns the live worker.
        assert returned.wait(5)
        assert len(outputs) == 1
        fresh.release.set()
    finally:
        fresh.release.set()
        for caller in callers:
            caller.join(5)
            assert not caller.is_alive()
    assert sum("EARLIER-A" in output for output in outputs) == 1
    assert provider.calls == [(A, "session"), (B, "session")]


def test_captured_context_and_combined_spill_policy(harness):
    manager, provider = ready_a(harness, value="A" * 90)
    provider.plans[B] = Recall("B" * 90)
    manager._external_prefetch_spill_config = {
        "enabled": True, "max_chars": 140, "preview_head": 30, "preview_tail": 30,
        "directory": str(harness.home / "spills"),
    }
    token = probe.set("owning-context")
    try:
        output = manager.prefetch_all(B, session_id="session")
    finally:
        probe.reset(token)
    assert "output truncated" in output
    spills = list((harness.home / "spills" / "session").glob("*.txt"))
    assert len(spills) == 1
    full = spills[0].read_text(encoding="utf-8")
    assert "A" * 90 in full and "B" * 90 in full
    assert "earlier-turn recall" in full.lower()
    assert provider.contexts[-1] == "owning-context"


@pytest.mark.parametrize("source", ["config-loader", "constructor"])
def test_config_loader_factory_and_constructor_enable_the_same_behavior(harness, source):
    harness.home.mkdir(parents=True, exist_ok=True)
    (harness.home / "config.yaml").write_text(
        "memory:\n  defer_prefetch_on_timeout: true\n  external_prefetch_timeout: 0.01\n", encoding="utf-8",
    )
    manager = (mm.memory_manager_from_config(load_config()["memory"]) if source == "config-loader" else
               mm.MemoryManager(defer_prefetch_on_timeout=True, external_prefetch_timeout=0.01))
    late = Recall("CONFIGURED-A", blocked=True)
    provider = Provider({A: late, B: Recall("FRESH-B")})
    manager.add_provider(provider)
    try:
        assert manager.prefetch_all(A, session_id="session") == ""
        finish(harness, late)
        manager._external_prefetch_timeout = 5
        assert "CONFIGURED-A" in manager.prefetch_all(B, session_id="session")
    finally:
        late.release.set()
        manager.shutdown_all()


def test_spilled_carry_keeps_origin_label_even_with_no_preview(harness):
    manager, provider = ready_a(harness)
    provider.plans[B] = Recall("FRESH-B" * 30)
    manager._external_prefetch_spill_config = {
        "enabled": True, "max_chars": 40, "preview_head": 0, "preview_tail": 0,
        "directory": str(harness.home / "spills"),
    }
    result = manager.prefetch_all(B, session_id="session")
    assert "output truncated" in result
    assert "earlier-turn recall" in result.lower() and "previous question" in result.lower()


def test_ambient_profile_change_invalidates_carry_without_reinitialization(harness, tmp_path):
    manager, provider = ready_a(harness)
    provider.plans[A] = Recall("NEW-PROFILE")
    token = set_hermes_home_override(tmp_path / "other-profile")
    try:
        result = manager.prefetch_all(A, session_id="session")
        assert "EARLIER-A" not in result and "NEW-PROFILE" in result
    finally:
        reset_hermes_home_override(token)


def test_ready_a_survives_gate_denial_and_local_ownership_recovers(harness, monkeypatch):
    manager, provider = ready_a(harness)
    provider.plans[B] = Recall("FRESH-B")
    with monkeypatch.context() as patch:
        patch.setattr(mm, "_MAX_INFLIGHT_CALLS", 0)
        assert "EARLIER-A" in manager.prefetch_all(B, session_id="session")
    assert provider.calls == [(A, "session")]
    assert manager.prefetch_all(B, session_id="session") == "FRESH-B"
