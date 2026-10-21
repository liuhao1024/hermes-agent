"""A preclaim ``admission_conflict`` settles the stale FIFO head instead of stranding the session.

The routing self-heal (#54878) drops and recreates a stale sessions.json entry without moving the
old session's queued admissions; every later drain then refuses the orphaned head with
``admission_conflict`` and pauses, so the session id answers 409 forever (#142731). The head must
settle cancelled and the turn behind it claim.
"""
import pytest


def _api_owner(authority, session_id):
    from gateway.config import Platform
    from gateway.session import SessionSource
    from gateway.session_authority import LiveSession
    from gateway.session_contract import SessionRef

    authority.db.create_session(session_id, source='api_server')
    source = SessionSource(platform=Platform.API_SERVER, chat_id=session_id, user_id='api', chat_type='dm')
    authority.sessions[session_id] = LiveSession(source, 'agent:default:api_server:dm:' + session_id)
    return SessionRef('default', session_id), authority.sessions[session_id]


@pytest.mark.asyncio
async def test_conflicted_head_settles_and_successor_is_claimed(owner, monkeypatch):
    from hermes_state_runtime import RuntimeStoreError, admit_session_input, list_session_admissions
    import gateway.session_api_turn

    session_id = 'hermes-review-owner-repo-1'
    ref, live = _api_owner(owner, session_id)
    admit_session_input(owner.db, epoch=owner.epoch, principal_id='api', session_id=session_id,
                        request_id='stale-1', payload={'text': 'review round 1', 'api_turn_v1': {'stale': True}})
    admit_session_input(owner.db, epoch=owner.epoch, principal_id='api', session_id=session_id,
                        request_id='fresh-2', payload={'text': 'review round 2', 'api_turn_v1': {'stale': False}})

    def check(_authority, _ref, payload):
        if payload['api_turn_v1']['stale']:
            raise RuntimeStoreError('admission_conflict')  # the self-healed binding never matches again
        return object()  # the successor's own preclaim passed

    monkeypatch.setattr(gateway.session_api_turn, 'check_api_turn', check)

    claimed = await owner._claim_with_retry(ref, live)

    rows = {row['request_id']: row for row
            in list_session_admissions(owner.db, session_id=session_id, pending_only=False)}
    assert (rows['stale-1']['status'], rows['stale-1']['outcome']) == ('terminal', 'cancelled')
    assert claimed is not None, 'the FIFO must advance, not pause the session for good'
    row, _first = claimed
    assert row['request_id'] == 'fresh-2'
    assert row['status'] == 'started'


@pytest.mark.asyncio
async def test_conflicted_pause_still_holds_when_head_cannot_settle(owner, monkeypatch):
    """A conflict raised while the head is no longer queued-cancelable (claimed, steering) must
    fall back to the pause, never cancel a live turn."""
    from gateway.session_authority import SessionAuthority
    from hermes_state_runtime import RuntimeStoreError

    session_id = 'hermes-review-owner-repo-2'
    ref, live = _api_owner(owner, session_id)
    monkeypatch.setattr(SessionAuthority, '_claim_next',
                        lambda self, ref, live: _raise(RuntimeStoreError('admission_conflict')))
    # No queued row to settle: the pause must stand.
    claimed = await owner._claim_with_retry(ref, live)
    assert claimed is None


async def _raise(exc):
    raise exc
