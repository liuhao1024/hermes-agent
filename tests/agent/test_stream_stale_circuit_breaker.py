"""Cross-turn stream-stale circuit breaker (issue #58962).

A session wedged against an unresponsive provider can hit the stale-stream
detector on every turn and loop forever, burning the full 180s×retries each
turn with no response (observed: 494 consecutive failures over 3+ days).

These tests cover the guard added to ``interruptible_streaming_api_call``:

- a session that has already tripped the consecutive-stale threshold short
  circuits immediately (no network attempt, no 180s wait) with a clear error;
- a successful stream resets the consecutive-stale streak;
- a stale-stream kill increments the consecutive-stale streak.

The harness mirrors tests/agent/test_anthropic_stream_pool_cleanup.py.
"""

import threading

import httpx
import pytest
from unittest.mock import MagicMock

from types import SimpleNamespace


def _make_anthropic_agent(**kwargs):
    from run_agent import AIAgent

    defaults = dict(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="claude-opus-4-7",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
    )
    defaults.update(kwargs)
    agent = AIAgent(**defaults)
    agent.api_mode = "anthropic_messages"
    agent._anthropic_client = MagicMock()
    agent._anthropic_api_key = "test-anthropic-key"
    # #67142: anthropic streams now run on a request-local client; route it to
    # the test mock so .messages.stream is exercised.
    agent._create_request_anthropic_client = lambda *a, **k: agent._anthropic_client
    return agent


def _good_stream_cm():
    """Context manager whose stream completes (message_stop) and returns a valid message."""
    cm = MagicMock()
    stream = MagicMock()
    stream.__iter__ = MagicMock(return_value=iter([SimpleNamespace(type="message_stop")]))
    msg = MagicMock()
    msg.content = []
    msg.stop_reason = "end_turn"
    msg.usage = SimpleNamespace(input_tokens=10, output_tokens=5)
    stream.get_final_message = MagicMock(return_value=msg)
    cm.__enter__ = MagicMock(return_value=stream)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


class TestStreamStaleCircuitBreaker:
    def test_interrupted_pre_response_wait_advances_streak(self, monkeypatch):
        """Qualified pre-response interrupts advance the breaker, while early
        and mid-stream user cancellations remain neutral."""
        from agent.chat_completion_helpers import (
            _check_stale_giveup,
            _record_interrupted_provider_wait,
        )

        monkeypatch.setenv("HERMES_STREAM_STALE_GIVEUP", "2")
        agent = _make_anthropic_agent()
        agent._consecutive_stale_streams = 0

        assert _record_interrupted_provider_wait(agent, 29.9, response_started=False) is False
        assert _record_interrupted_provider_wait(agent, 45.0, response_started=True) is False
        assert agent._consecutive_stale_streams == 0

        assert _record_interrupted_provider_wait(agent, 45.0, response_started=False) is True
        assert agent._consecutive_stale_streams == 1

        assert _record_interrupted_provider_wait(agent, 60.0, response_started=False) is True
        with pytest.raises(RuntimeError, match="2 consecutive stale attempts"):
            _check_stale_giveup(agent)

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    def test_short_circuits_when_streak_at_threshold(self, monkeypatch):
        """A session already past the consecutive-stale threshold must abort
        immediately without opening a stream or waiting out the stale timeout."""
        monkeypatch.setenv("HERMES_STREAM_STALE_GIVEUP", "3")

        agent = _make_anthropic_agent()
        agent._consecutive_stale_streams = 3  # simulate prior wedged turns

        # The stream must never be opened on the short-circuit path.
        with pytest.raises(RuntimeError, match="unresponsive"):
            agent._interruptible_streaming_api_call({})

        agent._anthropic_client.messages.stream.assert_not_called()
        # The streak is NOT reset on the short-circuit so subsequent turns
        # keep failing fast instead of re-attempting forever.
        assert agent._consecutive_stale_streams == 3

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    def test_success_resets_streak(self, monkeypatch):
        """A stream that completes successfully clears the consecutive-stale
        streak so a recovered provider resumes normally."""
        monkeypatch.setenv("HERMES_STREAM_STALE_GIVEUP", "3")

        agent = _make_anthropic_agent()
        agent._consecutive_stale_streams = 2  # below the giveup=3 threshold
        agent._anthropic_client.messages.stream.return_value = _good_stream_cm()

        resp = agent._interruptible_streaming_api_call({})
        assert resp is not None
        assert agent._consecutive_stale_streams == 0

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    def test_stale_kill_increments_streak(self, monkeypatch):
        """Each stale-stream kill increments the consecutive-stale streak so a
        wedged session eventually trips the breaker."""
        monkeypatch.setenv("HERMES_STREAM_STALE_TIMEOUT", "0.1")
        monkeypatch.setenv("HERMES_STREAM_STALE_GIVEUP", "50")

        agent = _make_anthropic_agent()
        agent._consecutive_stale_streams = 0
        unblock = threading.Event()

        def _blocking_gen():
            unblock.wait(timeout=5.0)
            raise httpx.ConnectError("connection dropped after close()")
            yield  # make this a generator so next() triggers the wait

        def _stream_side_effect(*args, **kwargs):
            cm = MagicMock()
            stream = MagicMock()
            stream.__iter__ = MagicMock(return_value=_blocking_gen())
            cm.__enter__ = MagicMock(return_value=stream)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        # Every attempt blocks, trips the stale detector, and fails.
        agent._anthropic_client.messages.stream.side_effect = _stream_side_effect
        # #67142: the stale detector now aborts the request-local client's
        # sockets from the poll thread (not close() on the shared client), so
        # unblock on the abort to simulate the socket shutdown waking the read.
        agent._abort_request_anthropic_client = lambda *a, **k: unblock.set()

        with pytest.raises(Exception):
            agent._interruptible_streaming_api_call({})

        # At least one stale kill happened; the streak must have advanced.
        assert agent._consecutive_stale_streams >= 1

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    @pytest.mark.parametrize("slow", ["worker_start", "worker_unwind"])
    def test_one_attempt_counts_once_however_often_the_timer_fires(self, monkeypatch, caplog, slow):
        """The stale timer re-fires every window while the worker has not started its
        attempt yet (CPU-starved host) or has not unwound a kill; one attempt must still
        count once, or a single wedged turn trips the breaker for the next one."""
        from agent import chat_completion_helpers as h

        monkeypatch.setenv("HERMES_STREAM_STALE_TIMEOUT", "0.1")
        monkeypatch.setenv("HERMES_STREAM_STALE_GIVEUP", "50")
        monkeypatch.setenv("HERMES_STREAM_RETRIES", "0")
        agent = _make_anthropic_agent()
        agent._consecutive_stale_streams = 0
        unblock = threading.Event()

        if slow == "worker_start":
            start = h._StreamingCall._start_stream_attempt

            def _late_start(self):
                threading.Event().wait(1.0)
                return start(self)

            monkeypatch.setattr(h._StreamingCall, "_start_stream_attempt", _late_start)
            agent._abort_request_anthropic_client = lambda *a, **k: unblock.set()
        else:
            agent._abort_request_anthropic_client = lambda *a, **k: None  # the kill never wakes the read

        def _blocking_gen():
            unblock.wait(timeout=1.2)
            raise httpx.ConnectError("connection dropped")
            yield

        def _stream_side_effect(*args, **kwargs):
            cm = MagicMock()
            stream = MagicMock()
            stream.__iter__ = MagicMock(return_value=_blocking_gen())
            cm.__enter__ = MagicMock(return_value=stream)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        agent._anthropic_client.messages.stream.side_effect = _stream_side_effect
        with caplog.at_level("WARNING", logger="agent.chat_completion_helpers"), pytest.raises(Exception):
            agent._interruptible_streaming_api_call({})

        kills = sum("Stream stale for" in r.getMessage() for r in caplog.records)
        assert kills >= 2, f"scenario needs the timer to re-fire; saw {kills} kill(s)"
        assert agent._consecutive_stale_streams == 1

    def test_rekill_counter_tracks_and_resets_per_attempt(self, monkeypatch):
        """The re-kill ledger is per-attempt: first kill counts the attempt once
        (existing contract), further kills of it increment the re-kill count, and
        a new attempt starts from zero (#141098)."""
        import threading as _threading

        from agent.chat_completion_helpers import _StreamingCall

        monkeypatch.setenv("HERMES_STREAM_STALE_REKILL_GIVEUP", "3")
        call = SimpleNamespace(
            agent=SimpleNamespace(),
            stream_attempt_lock=_threading.Lock(),
            stream_attempt_state={"current": 1, "cancelled": set()},
            _stale_counted_attempts=set(),
            _stale_rekill_counts={},
        )
        call._uncounted_stale_attempt = lambda: _StreamingCall._uncounted_stale_attempt(call)
        call._stale_rekill_count = lambda: _StreamingCall._stale_rekill_count(call)

        _StreamingCall._count_stale_attempt(call)  # first kill: counts the attempt
        assert call._stale_counted_attempts == {1}
        assert call.agent._consecutive_stale_streams == 1
        assert _StreamingCall._stale_rekill_wedged(call) is False

        for _ in range(3):  # three re-kills of the same attempt
            _StreamingCall._count_stale_attempt(call)
        assert _StreamingCall._stale_rekill_count(call) == 3
        assert _StreamingCall._stale_rekill_wedged(call) is True

        call.stream_attempt_state["current"] = 2  # the worker finally unwound; retry
        assert _StreamingCall._stale_rekill_wedged(call) is False

        monkeypatch.setenv("HERMES_STREAM_STALE_REKILL_GIVEUP", "0")  # 0 disables the wedged verdict
        call.stream_attempt_state["current"] = 1
        assert _StreamingCall._stale_rekill_wedged(call) is False

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    def test_rekill_giveup_stops_faking_liveness(self, monkeypatch, caplog):
        """Once re-kills of one attempt pass HERMES_STREAM_STALE_REKILL_GIVEUP the
        kill is not landing (#141098): the watchdog must stop touching activity —
        those touches kept the kanban heartbeat fresh through 3.5 days of 4-minute
        re-kills — while the breaker's one-attempt-one-count contract stays intact."""
        monkeypatch.setenv("HERMES_STREAM_STALE_TIMEOUT", "0.05")
        monkeypatch.setenv("HERMES_STREAM_STALE_GIVEUP", "50")
        monkeypatch.setenv("HERMES_STREAM_STALE_REKILL_GIVEUP", "2")
        monkeypatch.setenv("HERMES_STREAM_RETRIES", "0")
        agent = _make_anthropic_agent()
        agent._consecutive_stale_streams = 0
        touches: list[str] = []
        agent._touch_activity = lambda desc, **k: touches.append(desc)
        unblock = threading.Event()

        def _blocking_gen():
            unblock.wait(timeout=2.0)
            raise httpx.ConnectError("connection dropped")
            yield

        def _stream_side_effect(*args, **kwargs):
            cm = MagicMock()
            stream = MagicMock()
            stream.__iter__ = MagicMock(return_value=_blocking_gen())
            cm.__enter__ = MagicMock(return_value=stream)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        agent._anthropic_client.messages.stream.side_effect = _stream_side_effect
        # The kill never wakes the read: the reader stays wedged for the whole call.
        agent._abort_request_anthropic_client = lambda *a, **k: None

        with caplog.at_level("WARNING", logger="agent.chat_completion_helpers"), pytest.raises(Exception):
            agent._interruptible_streaming_api_call({})

        kills = sum("Stream stale for" in r.getMessage() for r in caplog.records)
        assert kills >= 4, f"scenario needs re-kills past the threshold; saw {kills} kill(s)"
        # First kill + re-kills below the threshold touch; wedged re-kills never do.
        kill_touches = [t for t in touches if t.startswith("stale stream detected")]
        assert len(kill_touches) == 2, f"expected touches only before the wedged verdict, saw {len(kill_touches)}"
        escalated = sum("without the reader unwinding" in r.getMessage() for r in caplog.records)
        assert escalated == 1  # the wedged verdict lands once, not per re-kill
        assert agent._consecutive_stale_streams == 1  # one attempt still counts once

    def test_heartbeat_silent_once_rekill_wedged(self):
        """The monitor heartbeat must also stop touching activity once the attempt
        is wedged, or the 30s gateway heartbeat alone would keep faking liveness."""
        import time as _time

        from agent.chat_completion_stream_monitor import StreamingWaitMonitor

        def _ns(wedged: bool) -> SimpleNamespace:
            return SimpleNamespace(
                agent=MagicMock(),
                _stream_stale_timeout=180.0,
                last_chunk_time={"t": _time.time() - 75.0},
                clients=None,
                _mon=SimpleNamespace(
                    wait_notice=MagicMock(), wait_notice_started_ts=None, last_heartbeat=_time.time()),
                _stale_rekill_wedged=lambda: wedged,
            )

        wedged = _ns(True)
        StreamingWaitMonitor._heartbeat(wedged, 75)
        wedged.agent._touch_activity.assert_not_called()
        wedged.agent._emit_wait_notice.assert_not_called()

        flowing = _ns(False)
        flowing._mon.wait_notice.should_emit = MagicMock(return_value=False)
        StreamingWaitMonitor._heartbeat(flowing, 75)
        flowing.agent._touch_activity.assert_called_once()
