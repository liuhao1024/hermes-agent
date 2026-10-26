"""An explicit stop of a canonical admitted run reaps the background processes its turn created.

The legacy in-process stop branch (``api_server_runs._handle_stop_run``) interrupts *and* reaps;
the session-authority branch (``api_server_authority_runs.stop_run``) only interrupted, so a
``background`` terminal process the run spawned survived the API-reported cancellation (#144761).
Only the agent the stopped generation adopted may be reaped from: a cached resident agent belongs
to an earlier turn, and a replacement claim owns its own baseline.
"""
import time
from types import SimpleNamespace

import pytest

from hermes_state_runtime import claim_session_input, list_session_admissions
from tests.gateway.test_api_unknown_resolution import _AUTH_HEADERS, _runs_client
from tests.waiting import wait_until


def _adopted_turn_agent(**extra):
    """A fake adopted agent carrying the turn-process ownership attributes canonical own-agent
    turns publish (``run_turn_runner`` sets task_id and baseline; canonical turns pass their own
    claim check instead of an epoch)."""
    agent = SimpleNamespace(interrupt=lambda *a, **k: None,
                            _gateway_turn_process_task_id="sess-api-session",
                            _gateway_turn_process_baseline=frozenset({"pre-existing"}))
    for key, value in extra.items():
        setattr(agent, key, value)
    return agent


async def _admit_and_start_run(api, owner):
    """Admit a /v1/runs turn and claim its head to ``started`` -> (client, run_id, row)."""
    client = _runs_client(api)
    await client.start_server()
    accepted = await client.post('/v1/runs', json={'input': 'spawn-then-stop', 'session_id': 'api-session'},
                                 headers={**_AUTH_HEADERS, 'Idempotency-Key': 'authority-stop-reap'})
    assert accepted.status == 202, await accepted.text()
    run_id = (await accepted.json())['run_id']
    claim_session_input(owner.db, epoch=owner.epoch, session_id='api-session')
    row = list_session_admissions(owner.db, session_id='api-session')[0]
    assert row['status'] == 'started', row
    return client, run_id, row


@pytest.mark.asyncio
async def test_stop_of_started_admission_reaps_the_turn_it_adopted(api, owner, monkeypatch):
    from gateway.platforms import api_server as api_server_module
    reaps = []
    monkeypatch.setattr(
        api_server_module, "_reap_disconnected_agent_processes",
        lambda agent, *, source, is_still_current=None: reaps.append((agent, source, is_still_current)))
    agent = _adopted_turn_agent()
    client, run_id, row = await _admit_and_start_run(api, owner)
    try:
        owner.adopted[row['target_session_id']] = (row['generation'], agent)

        stopped = await client.post(f'/v1/runs/{run_id}/stop', headers=_AUTH_HEADERS)
        body = await stopped.json()
        assert stopped.status == 200, body
        assert body['status'] == 'stopping', body
        wait_until(lambda: reaps, what="stop reaped the adopted turn's processes")
        reaped_agent, source, still_current = reaps[0]
        assert reaped_agent is agent
        assert source == "api_server_run_stop"
        # The claim check rides into the reaper thread: current while this generation holds the
        # session, stale the moment a replacement claim adopts its own agent.
        assert still_current() is True
        owner.adopted[row['target_session_id']] = (row['generation'] + 1, _adopted_turn_agent())
        assert still_current() is False
    finally:
        for task in list(api._active_run_tasks.values()):
            task.cancel()
        await client.close()


@pytest.mark.asyncio
async def test_stop_without_the_adopted_generation_reaps_nothing(api, owner, monkeypatch):
    from gateway.platforms import api_server as api_server_module
    reaps = []
    monkeypatch.setattr(
        api_server_module, "_reap_disconnected_agent_processes",
        lambda agent, *, source, is_still_current=None: reaps.append((agent, source)))
    client, run_id, row = await _admit_and_start_run(api, owner)
    try:
        # The resident agent belongs to an earlier generation's turn: Stop still lands on it
        # (latched), but its process baseline is not this run's to reap.
        owner.runner._cached_agent_for = lambda route: _adopted_turn_agent()
        owner.adopted[row['target_session_id']] = (row['generation'] + 5, _adopted_turn_agent())

        stopped = await client.post(f'/v1/runs/{run_id}/stop', headers=_AUTH_HEADERS)
        assert stopped.status == 200, await stopped.text()
        assert reaps == []
    finally:
        for task in list(api._active_run_tasks.values()):
            task.cancel()
        await client.close()


def test_reap_wrapper_keeps_the_caller_claim_check_even_for_epoch_agents(monkeypatch):
    """A caller-supplied ``is_still_current`` must reach the reaper even when the agent carries a
    published epoch — canonical turns pass their own claim check, and the epoch gate must not
    silently replace it."""
    from gateway import run as gateway_run
    seen = {}

    def fake_reaper(task_id, baseline, *, source, is_still_current=None):
        seen.update(task_id=task_id, baseline=baseline, source=source, is_still_current=is_still_current)

    monkeypatch.setattr(gateway_run, "_reap_gateway_turn_processes", fake_reaper)
    from gateway.platforms.api_server import _reap_disconnected_agent_processes

    def claim():
        return False

    agent = _adopted_turn_agent(_gateway_turn_process_epoch=99)
    _reap_disconnected_agent_processes(agent, source="api_server_run_stop", is_still_current=claim)
    wait_until(lambda: seen, what="reaper thread ran with the caller's claim check")
    assert seen == {"task_id": "sess-api-session", "baseline": frozenset({"pre-existing"}),
                    "source": "api_server_run_stop", "is_still_current": claim}


def test_reap_wrapper_still_epoch_gates_without_a_caller_check(monkeypatch):
    """Existing behavior: with no caller check, an epoch-published agent reaps under the epoch
    gate; an agent without ownership attributes is a no-op."""
    from gateway import run as gateway_run
    seen = []
    monkeypatch.setattr(
        gateway_run, "_reap_gateway_turn_processes",
        lambda task_id, baseline, *, source, is_still_current=None:
            seen.append((task_id, baseline, source, is_still_current)))
    from gateway.platforms.api_server import _reap_disconnected_agent_processes

    # No ownership attributes -> nothing to reap, no thread.
    _reap_disconnected_agent_processes(SimpleNamespace(), source="api_server_run_stop")
    # health: allow HX013 -- the no-op branch must not even start a reaper thread; this delay bounds that negative assertion
    time.sleep(0.05)
    assert seen == []

    agent = _adopted_turn_agent(_gateway_turn_process_epoch=7)
    _reap_disconnected_agent_processes(agent, source="api_server_run_stop")
    wait_until(lambda: seen, what="epoch-gated reaper ran")
    task_id, baseline, source, still_current = seen[0]
    assert (task_id, baseline, source) == ("sess-api-session", frozenset({"pre-existing"}), "api_server_run_stop")
    # The epoch gate answers against the shared epoch table, not the agent's own epoch value.
    assert callable(still_current) and still_current() is True
