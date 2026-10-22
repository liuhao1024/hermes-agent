"""A Stop naming a generation whose turn already returned must not reach the next claim's agent.

Own-agent runs (/v1/runs) install their agent in the route's running slot at creation, before
adoption; while a finished turn's settlement lags (its admission still reading ``started``), a
Stop delivered to that resident agent cancels the NEXT turn instead (#143290). The generation
fence is ``executing_returned``: stamped the moment a claim's execution returns, cleared with
its settlement."""
import asyncio
from types import SimpleNamespace

import pytest

from hermes_state_runtime import admit_session_input, claim_session_input
from tests.gateway.test_reaped_eviction_interrupts_run import _build_gateway, KEY
from tests.gateway.test_session_authority_cancel_settlement import ACTOR, REF, _authority, _submit


def _stopped_agent(calls):
    return SimpleNamespace(interrupt=lambda: calls.append('stop'),
                           clear_interrupt=lambda: calls.append('clear'))


@pytest.mark.asyncio
async def test_stop_for_returned_generation_spares_the_next_claims_agent(tmp_path, monkeypatch):
    db, authority = _authority(tmp_path, monkeypatch)
    calls = []
    agent = _stopped_agent(calls)
    runner, _state = _build_gateway(agent, [])
    authority.runner = runner
    authority.sessions['s'].route = KEY
    with db:
        admit_session_input(db, epoch=authority.epoch, principal_id=ACTOR.subject, session_id='s',
                            request_id='finished', payload={'text': 'work'})
        row = claim_session_input(db, epoch=authority.epoch, session_id='s')
        # The claimed turn returned while its settlement lags: the admission still reads
        # ``started``, and the route's slot already holds the NEXT run's agent.
        authority.executing_returned['s'] = row['generation']
        assert authority._handle(REF).execution_state == 'running'
        assert runner._resident_agent_for(KEY) is agent
        handle = await authority.interrupt(ACTOR, REF, row['generation'])
        assert handle.execution_state == 'running'
        assert calls == [], "the next claim's agent must not take a Stop for the returned generation"
        assert authority.pending_stops.get('s') == row['generation'], (
            'the Stop latches for adopt_agent instead')


@pytest.mark.asyncio
async def test_stop_while_executing_still_reaches_the_resident_agent(tmp_path, monkeypatch):
    db, authority = _authority(tmp_path, monkeypatch)
    calls = []
    agent = _stopped_agent(calls)
    runner, _state = _build_gateway(agent, [])
    authority.runner = runner
    authority.sessions['s'].route = KEY
    with db:
        admit_session_input(db, epoch=authority.epoch, principal_id=ACTOR.subject, session_id='s',
                            request_id='running', payload={'text': 'work'})
        row = claim_session_input(db, epoch=authority.epoch, session_id='s')
        # Executing (or claimed but not yet stamped, as a Stop racing the claim reads it): the
        # resident agent may be the one this generation runs, so the Stop still goes to it.
        await authority.interrupt(ACTOR, REF, row['generation'])
        assert calls == ['stop']


@pytest.mark.asyncio
async def test_latched_stop_never_fires_for_a_later_generation(tmp_path, monkeypatch):
    db, authority = _authority(tmp_path, monkeypatch)
    calls = []
    agent = _stopped_agent(calls)
    runner, _state = _build_gateway(agent, [])
    authority.runner = runner
    authority.sessions['s'].route = KEY
    with db:
        admit_session_input(db, epoch=authority.epoch, principal_id=ACTOR.subject, session_id='s',
                            request_id='finished', payload={'text': 'work'})
        row = claim_session_input(db, epoch=authority.epoch, session_id='s')
        authority.executing_returned['s'] = row['generation']
        await authority.interrupt(ACTOR, REF, row['generation'])  # latches: no agent delivery
        # The next claim adopts its own agent: the latched Stop for the older generation must
        # neither interrupt it nor read as a delivered Stop that adopt_agent must clear.
        authority.adopt_agent('s', row['generation'] + 1, agent)
        assert calls == []
        assert authority.pending_stops.get('s') == row['generation']
        # The exact generation adopting later still takes its latched Stop.
        authority.adopt_agent('s', row['generation'], agent)
        assert calls == ['stop']


@pytest.mark.asyncio
async def test_returned_stamp_is_cleared_with_the_settlement(tmp_path, monkeypatch):
    from gateway import session_finite

    db, authority = _authority(tmp_path, monkeypatch)
    monkeypatch.setattr(session_finite, 'execute_finite_admission',
                        lambda authority, ref, row: asyncio.sleep(0, result='done'))
    with db:
        await _submit(authority, 'work')
        await asyncio.wait_for(authority.sessions['s'].task, 5)
        # Neither the executing stamp nor its returned tombstone survives a settled turn.
        assert not authority.executing
        assert not authority.executing_returned
