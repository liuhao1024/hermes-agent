"""A due heartbeat fires in a desktop ("local") chat.

``_heartbeat_poll_watch`` returned early on ``not adapter._message_handler``. ``LocalSessionAdapter`` never has
a message handler, although the authority path it then takes (``admit_heartbeat`` → ``admit_automation``)
does not use one, so every heartbeat of a desktop chat stayed at fire_count 0.
"""
import time

import pytest

from tests.gateway.test_session_end_delegations import _local


def _arm_due(session_id):
    from hermes_cli.heartbeat import HeartbeatManager, load_heartbeat, save_heartbeat
    HeartbeatManager(session_id).set('status tick', 60)
    state = load_heartbeat(session_id)
    state.created_at = time.time() - 120   # one interval overdue
    save_heartbeat(session_id, state)


def _heartbeat_admissions(authority, ref):
    from hermes_state_runtime import list_session_admissions
    return [r for r in list_session_admissions(authority.db, session_id=ref.session_id, pending_only=False)
            if r['payload'].get('local_automation_v1', {}).get('heartbeat')]


@pytest.mark.asyncio
async def test_due_heartbeat_is_admitted_into_a_local_session(tmp_path, monkeypatch):
    from gateway.run_heartbeat_restore import restore_heartbeat_watches
    runner, authority, ref, _actor = await _local(tmp_path, monkeypatch)
    try:
        live = authority.sessions[ref.session_id]
        adapter = runner._adapter_for_source(live.source)
        assert adapter._message_handler is None  # the production shape of LocalSessionAdapter
        _arm_due(ref.session_id)
        await restore_heartbeat_watches(runner)
        watch = getattr(runner, '_heartbeat_watch', None) or {}
        assert watch, 'the local heartbeat must be watched'
        await runner._heartbeat_poll_once(watch)
        assert len(_heartbeat_admissions(authority, ref)) == 1
        await runner._heartbeat_poll_once(watch)   # a pending tick coalesces, never stacks
        assert len(_heartbeat_admissions(authority, ref)) == 1
    finally:
        authority.db.close()


@pytest.mark.asyncio
async def test_due_heartbeat_runs_as_a_turn_for_a_viewed_local_session(tmp_path, monkeypatch):
    from gateway.run_heartbeat_restore import restore_heartbeat_watches
    from tests.gateway.test_session_end_delegations import _record_turns
    runner, authority, ref, actor = await _local(tmp_path, monkeypatch)
    try:
        seen = _record_turns(runner)
        authority.sessions[ref.session_id].subscribers['viewer'] = actor
        _arm_due(ref.session_id)
        await restore_heartbeat_watches(runner)
        await runner._heartbeat_poll_once(runner._heartbeat_watch)
        await authority._drain(ref)
        assert any('status tick' in str(text) for text in seen), seen
    finally:
        authority.db.close()
