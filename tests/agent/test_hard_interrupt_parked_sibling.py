"""Regression tests for #145255: an interrupt fired from inside a trusted pre-dispatch
callback must stop an already-authorized sibling that is parked at the start-order gate.

Timeline that used to leak a dispatch: the sibling passes the authorization gate and its
guardrails, then parks at ``begin_in_order``; the first-listed call is blocked by its own
guardrail, which interrupts the turn before returning the block. The block advances the
start order without a callback, the parked sibling wakes up, and
``_dispatch_authorized_once`` crossed the dispatch edge without re-checking cancellation —
the tool started ~26ms after the interrupt had already been recorded. The outer batch
poll loop only abandons the gate on its next 5s slice, so it cannot close this window.

The interrupt is raised from the guardrail callback (outside the authorization gate's
serialization lock) so the harness never wedges the batch on that lock; a plugin
``pre_tool_call`` hook that interrupts sits behind the same dispatch edge.
"""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_hermes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    (tmp_path / ".hermes").mkdir(exist_ok=True)


def _make_agent(monkeypatch):
    """Minimal AIAgent-like stub, mirroring test_start_order_gate.py."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("HERMES_INFERENCE_PROVIDER", "")
    import run_agent as _ra

    class _Stub:
        _interrupt_requested = False
        _interrupt_message = None
        _execution_thread_id = threading.current_thread().ident
        _interrupt_thread_signal_pending = False
        log_prefix = ""
        quiet_mode = True
        verbose_logging = False
        log_prefix_chars = 200
        _checkpoint_mgr = MagicMock(enabled=False)
        tool_progress_callback = None
        tool_start_callback = None
        tool_complete_callback = None
        tool_progress_mode = "off"
        _todo_store = MagicMock()
        _session_db = None
        valid_tool_names = set()
        _turns_since_memory = 0
        _iters_since_skill = 0
        _current_tool = None
        _last_activity = 0
        _print_fn = print
        session_id = ""
        _current_turn_id = ""
        _current_api_request_id = ""
        _active_children: list = []

        def __init__(self):
            self._tool_worker_threads: set = set()
            self._tool_worker_threads_lock = threading.Lock()
            self._active_children_lock = threading.Lock()

        def _touch_activity(self, desc):
            self._last_activity = time.time()

        def _vprint(self, msg, force=False):
            pass

        def _safe_print(self, msg):
            pass

        def _should_emit_quiet_tool_messages(self):
            return False

        def _should_start_quiet_spinner(self):
            return False

        def _has_stream_consumers(self):
            return False

        def _tool_result_content_for_active_model(self, name, result):
            return result

        def _record_file_mutation_result(self, *a, **kw):
            pass

    stub = _Stub()
    stub._subdirectory_hints = MagicMock()
    stub._subdirectory_hints.check_tool_call = lambda *a, **kw: None
    stub._flush_messages_to_session_db = lambda *a, **kw: None
    stub._append_guardrail_observation = lambda name, result, *a, **kw: result
    stub._execute_tool_calls_concurrent = (
        _ra.AIAgent._execute_tool_calls_concurrent.__get__(stub)
    )
    stub.interrupt = _ra.AIAgent.interrupt.__get__(stub)
    stub.hard_interrupt = _ra.AIAgent.hard_interrupt.__get__(stub)
    stub.clear_interrupt = _ra.AIAgent.clear_interrupt.__get__(stub)
    stub._apply_pending_steer_to_tool_results = lambda *a, **kw: None
    stub._guardrail_block_result = lambda d: json.dumps(
        {"error": getattr(d, "message", None) or "blocked by guardrail policy"}
    )
    return stub


class _FakeToolCall:
    def __init__(self, name, call_id):
        self.function = MagicMock(name=name, arguments="{}")
        self.function.name = name
        self.id = call_id


class _FakeAssistantMsg:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


def _run_batch_with_guardrail_interrupt(monkeypatch, interrupt_fn):
    """Run a real concurrent batch of [tool_a, tool_b] where tool_a's guardrail waits
    until tool_b is parked at the start-order gate, then interrupts the turn and blocks
    tool_a. Returns (dispatched, messages)."""
    import agent.tool_executor as te

    agent = _make_agent(monkeypatch)
    monkeypatch.setattr(te, "_START_ORDER_GATE_TIMEOUT_S", 5.0)
    monkeypatch.setattr(te, "_resolve_concurrent_tool_timeout", lambda: 10.0)

    dispatched: list[str] = []
    sibling_parked = threading.Event()

    original_begin = te._StartOrderGate.begin_in_order

    def _park_probe(self, order, callback=None, *, tool_name=""):
        if order == 1:
            sibling_parked.set()
        return original_begin(self, order, callback, tool_name=tool_name)

    monkeypatch.setattr(te._StartOrderGate, "begin_in_order", _park_probe)

    def _pre_hook(tool_name, args, **kwargs):
        # Deliberately wait-free: hooks run inside the authorization gate's serialization
        # lock, and waiting there for the sibling would deadlock the batch instead of
        # exercising the dispatch edge.
        return None, None

    def _invoke(name, *a, **kw):
        dispatched.append(name)
        return json.dumps({"ok": name})

    agent._invoke_tool = MagicMock(side_effect=_invoke)
    decisions = {
        "tool_a": MagicMock(
            allows_execution=False, message="forbidden path requested by policy"
        ),
        "tool_b": MagicMock(allows_execution=True),
    }

    def _before_call(name, args):
        decision = decisions[name]
        if name == "tool_a":
            assert sibling_parked.wait(5.0), (
                "sibling never reached the start-order gate"
            )
            interrupt_fn(agent)
        return decision

    agent._tool_guardrails = MagicMock()
    agent._tool_guardrails.before_call = _before_call

    msg = _FakeAssistantMsg(
        [
            _FakeToolCall("tool_a", "tc_a"),
            _FakeToolCall("tool_b", "tc_b"),
        ]
    )
    messages: list = []
    with patch(
        "hermes_cli.plugins._dispatch_pre_tool_call_hooks", side_effect=_pre_hook
    ):
        agent._execute_tool_calls_concurrent(msg, messages, "task")
    agent.clear_interrupt()
    return dispatched, messages


def test_hard_interrupt_in_guardrail_stops_parked_authorized_sibling(monkeypatch):
    """A sibling already past authorization must not dispatch after hard_interrupt()."""
    dispatched, messages = _run_batch_with_guardrail_interrupt(
        monkeypatch,
        lambda agent: agent.hard_interrupt("policy: forbidden path requested"),
    )

    assert "tool_b" not in dispatched, (
        f"already-authorized sibling dispatched after hard_interrupt: {dispatched}"
    )
    by_tool = {m.get("name"): m["content"] for m in messages}
    assert "forbidden" in str(by_tool.get("tool_a", "")), (
        f"tool_a should carry its guardrail block: {by_tool.get('tool_a')!r}"
    )
    assert "cancel" in str(by_tool.get("tool_b", "")).lower(), (
        f"tool_b should be reported cancelled, got: {by_tool.get('tool_b')!r}"
    )


def test_soft_interrupt_in_guardrail_stops_parked_authorized_sibling(monkeypatch):
    """A soft interrupt from the same callback must close the same dispatch-edge window."""
    dispatched, messages = _run_batch_with_guardrail_interrupt(
        monkeypatch,
        lambda agent: agent.interrupt("stop requested"),
    )

    assert "tool_b" not in dispatched, (
        f"already-authorized sibling dispatched after soft interrupt: {dispatched}"
    )
    by_tool = {m.get("name"): m["content"] for m in messages}
    assert "forbidden" in str(by_tool.get("tool_a", "")), (
        f"tool_a should carry its guardrail block: {by_tool.get('tool_a')!r}"
    )
    assert "cancel" in str(by_tool.get("tool_b", "")).lower(), (
        f"tool_b should be reported cancelled, got: {by_tool.get('tool_b')!r}"
    )
