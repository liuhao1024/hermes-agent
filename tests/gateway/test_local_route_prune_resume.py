"""A live local session resumes after the idle prune drops its routing row (#143848).

``prune_old_entries`` drops routing entries idle by ``updated_at`` — a clock that only advances
on turns — so a session the user keeps opening but has not turned in within the window loses its
row while staying registered live. The live branch of ``restore_local_session`` then refused
every resume with ``admission_conflict`` forever, while the cold branch already re-adopted the
route from the receipt. The receipt now re-adopts it in both branches.
"""
from types import SimpleNamespace

import pytest


async def _local_session(tmp_path, monkeypatch):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_local import create_local_session

    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'platform_toolsets': {'cli': []}})
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
                             _cached_agent_for=lambda route: None, _adapter_for_source=lambda source: None)
    authority = await initialize_session_authority(runner, profile_id=str(tmp_path), instance_id='fixture')
    from gateway.session_controls import AuthorityConnection
    connection = AuthorityConnection(authority, object(), {'user_id': 'owner'})
    ref = create_local_session(authority, connection.actor, {'request_id': 'r', 'source': 'gui',
                                                             'cwd': str(tmp_path), 'model': 'm', 'toolsets': []})
    authority.db.append_message(ref.session_id, 'user', 'earlier turn')
    return store, authority, connection, ref


@pytest.mark.asyncio
async def test_resume_reattaches_a_route_pruned_under_a_live_session(tmp_path, monkeypatch):
    store, authority, connection, ref = await _local_session(tmp_path, monkeypatch)
    try:
        live = authority.sessions[ref.session_id]  # stays live: only the routing row is gone
        with store._lock:
            store._entries.pop(live.route, None)  # what prune_old_entries leaves behind

        reply = await connection.dispatch({'id': 1, 'method': 'session.resume',
                                           'params': {'session_id': ref.session_id}})

        assert reply['result']['messages'][0]['content'] == 'earlier turn', reply
        assert store._entries[live.route].session_id == ref.session_id
    finally:
        await connection.close()
        authority.db.close()
