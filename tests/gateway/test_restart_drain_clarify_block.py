"""Restart after-turn wait must not hold for turns blocked in a pending clarify (#145308).

A turn sitting at ``current_tool=clarify`` with an unlimited ``agent.clarify_timeout``
cannot finish on its own: the agent thread polls ``wait_for_response`` in one-second
slices, each touching the activity clock, so the wedge detector never fires and
``hermes gateway restart --yes`` defers for the whole ``restart_after_turn_timeout``
cap (1800 s by default) while refusing new turns. The pending answer dies with the
process anyway — the turn interrupt at ``stop()`` resolves the entry as cancelled —
so clarify-blocked turns are excluded from the wait, same as wedged ones.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.run import _AGENT_PENDING_SENTINEL
from gateway.session_state import SessionState
from tests.gateway.restart_test_helpers import make_restart_runner
from tools import clarify_gateway

KEY_BLOCKED = "agent:main:telegram:dm:111"
KEY_LIVE = "agent:main:telegram:dm:222"


def _live_agent(idle_seconds: float = 1.0) -> MagicMock:
    agent = MagicMock()
    agent.get_activity_summary = MagicMock(
        return_value={"seconds_since_activity": idle_seconds}
    )
    return agent


def _register_pending(session_key: str, clarify_id: str) -> None:
    clarify_gateway.register(
        clarify_id=clarify_id,
        session_key=session_key,
        question="q",
        choices=["a", "b"],
    )


@pytest.fixture(autouse=True)
def _clean_clarify_entries():
    yield
    clarify_gateway.clear_session(KEY_BLOCKED)
    clarify_gateway.clear_session(KEY_LIVE)


def test_clarify_blocked_count_counts_only_agents_behind_a_pending_entry(monkeypatch):
    monkeypatch.delenv("HERMES_AGENT_TIMEOUT", raising=False)
    runner, _adapter = make_restart_runner()
    assert runner._clarify_blocked_chat_agent_count() == 0

    _register_pending(KEY_BLOCKED, "c1")
    runner._running_agents[KEY_BLOCKED] = _live_agent()
    # A live turn without a pending entry must not be counted.
    runner._running_agents[KEY_LIVE] = _live_agent()

    assert runner._clarify_blocked_chat_agent_count() == 1
    # Only the KEY_LIVE turn stays awaitable; the blocked one is excluded.
    assert runner._awaitable_work_count() == 1
    # Pending entry behind the claim sentinel counts nothing (same skip as the wedge counter).
    runner._running_agents["agent:main:telegram:dm:333"] = _AGENT_PENDING_SENTINEL
    assert runner._clarify_blocked_chat_agent_count() == 1


@pytest.mark.asyncio
async def test_request_restart_skips_wait_when_only_clarify_blocked_turn(monkeypatch):
    """A turn blocked on a pending clarify must not pin the restart for the cap.

    Regression for #145308: with ``agent.clarify_timeout: 0`` the blocked turn keeps
    its activity fresh, so neither the wedge exclusion nor the drain ended the wait
    before the 1800 s cap."""
    monkeypatch.delenv("HERMES_AGENT_TIMEOUT", raising=False)
    runner, _adapter = make_restart_runner()
    runner.stop = AsyncMock()
    runner._restart_after_turn_timeout = 300.0
    _register_pending(KEY_BLOCKED, "c2")
    runner._running_agents[KEY_BLOCKED] = _live_agent()

    assert runner.request_restart(detached=False, via_service=True) is True
    await asyncio.wait_for(runner._restart_task, timeout=5.0)

    runner.stop.assert_awaited_once_with(
        restart=True, detached_restart=False, service_restart=True
    )
    # The agent stays in the map — stop() owns the interrupt, whose finally
    # resolves the pending entry as cancelled.
    assert KEY_BLOCKED in runner._running_agents
    assert clarify_gateway.has_pending(KEY_BLOCKED)


@pytest.mark.asyncio
async def test_request_restart_still_waits_for_live_turn_alongside_clarify_blocked(
    monkeypatch,
):
    """Mixed live + clarify-blocked: the live turn is honored, the blocked one ignored."""
    monkeypatch.delenv("HERMES_AGENT_TIMEOUT", raising=False)
    runner, _adapter = make_restart_runner()
    runner.stop = AsyncMock()
    runner._restart_after_turn_timeout = 300.0
    _register_pending(KEY_BLOCKED, "c3")
    runner._running_agents[KEY_BLOCKED] = _live_agent()
    runner._running_agents[KEY_LIVE] = _live_agent()

    assert runner.request_restart(detached=False, via_service=True) is True

    # Live turn active → stop() must not run yet, blocked turn notwithstanding.
    await asyncio.sleep(0.25)  # health: allow HX013 -- asserting the deferred wait is live is timing by contract
    runner.stop.assert_not_awaited()

    del runner._running_agents[KEY_LIVE]
    await asyncio.wait_for(runner._restart_task, timeout=5.0)
    runner.stop.assert_awaited_once()


def test_describe_active_work_marks_clarify_blocked_unit(monkeypatch):
    monkeypatch.delenv("HERMES_AGENT_TIMEOUT", raising=False)
    runner, _adapter = make_restart_runner()
    agent = _live_agent()
    runner._running_agents[KEY_BLOCKED] = agent
    _register_pending(KEY_BLOCKED, "c4")

    state = SessionState()
    state.turn.agent = agent
    state.turn.started_ts = 0.0
    runner._sessions_map()[KEY_BLOCKED] = state

    units = runner._describe_active_work()
    chat = [
        u for u in units if u.get("kind") == "chat" and u.get("session") == KEY_BLOCKED
    ]
    assert chat and chat[0].get("blocked_on") == "clarify"
