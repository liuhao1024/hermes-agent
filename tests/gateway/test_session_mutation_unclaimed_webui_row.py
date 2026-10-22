"""A metadata edit on a cold row no restore chain claims edits the row itself (#143110).

A retired WebUI install leaves ``source='webui'`` rows: they carry their id as the session key and
an origin no ``Platform`` answers to, so no restore chain ever claims them and every sidebar
action on them answered ``not_found``. Rename/archive/sidebar name the row, not a live session —
the CLI already edits those rows without a mount — so the mutation gate edits the row too, keeping
the session gate (and its ``not_found``) for rows a chain could still claim.
"""
from types import SimpleNamespace

import pytest


def _owner(tmp_path, monkeypatch):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import SessionAuthority
    from gateway.session_controls import AuthorityConnection
    import hermes_state_runtime as rt
    monkeypatch.setattr(run, '_load_gateway_config', lambda *a: {})
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    db = store._db
    runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
                             _evict_cached_agent=lambda route: None, _cached_agent_for=lambda route: None,
                             _handle_message=lambda event: 'ok',
                             _resolve_session_agent_runtime=lambda **k: ('frozen', {}))
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = SessionAuthority(runner, profile_id='owned', instance_id='owner', db=db,
                                 epoch=rt.begin_runtime_epoch(db, instance_id='owner'))
    owner = AuthorityConnection(authority, object(), {'user_id': 'human'})
    return store, db, authority, owner


def _cold_row(db, sid, *, source='webui', user_id=None, origin_json=True):
    """A cold session row exactly as a retired WebUI install left it: self-keyed, no chat id."""
    origin = (f'{{"platform": "{source}", "chat_id": "{sid}", "chat_type": "dm"}}'
              if origin_json else None)
    db._execute_write(lambda conn: conn.execute(
        'INSERT INTO sessions(id, source, session_key, chat_id, origin_json, user_id, started_at) '
        'VALUES(?,?,?,?,?,?,?)',
        (sid, source, sid, None, origin, user_id, 0.0)))


async def _mutate(owner, sid, request_id, operation, payload, revision=0):
    return await owner.dispatch({'id': 1, 'method': 'session.mutate', 'params': {
        'session_id': sid, 'request_id': request_id, 'expected_revision': revision,
        'operation': operation, 'payload': payload}})


@pytest.mark.asyncio
async def test_archive_and_rename_of_a_cold_webui_row_edit_the_row(tmp_path, monkeypatch):
    store, db, _authority, owner = _owner(tmp_path, monkeypatch)
    try:
        _cold_row(db, 'web-1')
        reply = await _mutate(owner, 'web-1', 'a1', 'archive', {'archived': True})
        assert 'result' in reply, reply
        assert db.get_session('web-1')['archived'] == 1
        renamed = await _mutate(owner, 'web-1', 'r1', 'rename', {'title': 'Kept'},
                                revision=db.get_session('web-1')['runtime_revision'])
        assert 'result' in renamed, renamed
        assert db.get_session('web-1')['title'] == 'Kept'
        # An exact retry replays the committed receipt.
        again = await _mutate(owner, 'web-1', 'a1', 'archive', {'archived': True})
        assert 'result' in again and again['result'] == reply['result'], again
    finally:
        await owner.close()
        store.close_all_db_handles()


@pytest.mark.asyncio
async def test_a_stamped_owner_keeps_its_unclaimed_row(tmp_path, monkeypatch):
    store, db, _authority, owner = _owner(tmp_path, monkeypatch)
    try:
        _cold_row(db, 'web-2', user_id='someone-else')
        reply = await _mutate(owner, 'web-2', 'a2', 'archive', {'archived': True})
        assert reply['error']['data']['reason'] == 'permission_denied', reply
        assert db.get_session('web-2')['archived'] == 0
    finally:
        await owner.close()
        store.close_all_db_handles()


@pytest.mark.asyncio
async def test_delete_of_an_unclaimed_row_still_needs_its_session_gate(tmp_path, monkeypatch):
    store, db, _authority, owner = _owner(tmp_path, monkeypatch)
    try:
        _cold_row(db, 'web-3')
        reply = await _mutate(owner, 'web-3', 'd3', 'delete', {})
        assert reply['error']['data']['reason'] == 'not_found', reply
        assert db.get_session('web-3') is not None
    finally:
        await owner.close()
        store.close_all_db_handles()


@pytest.mark.asyncio
async def test_a_messaging_row_keeps_its_not_found_verdict(tmp_path, monkeypatch):
    store, db, _authority, owner = _owner(tmp_path, monkeypatch)
    try:
        # No chat id and no origin to rebuild an entry from: the routed restore refuses it, so
        # the session gate (not the row-only path) answers — exactly as before this fix.
        _cold_row(db, 'tg-1', source='telegram', origin_json=False)
        reply = await _mutate(owner, 'tg-1', 'a4', 'archive', {'archived': True})
        assert reply['error']['data']['reason'] == 'not_found', reply
    finally:
        await owner.close()
        store.close_all_db_handles()
