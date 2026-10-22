"""A LOCAL ``notify+wake`` subscription's wake reaches the subscriber session FIFO (#143268).

The notifier delivers a push wake through ``deliver_wake``, whose canonical path
(``admit_route_wake``) needs the adapter's owner-runner reference. ``LocalSessionAdapter``
never bound one, so every LOCAL push wake fell back to ``admit_internal_event`` — a local
adapter has no classic inbound message handler — raised ``WakeNotAccepted``, and the notifier
rewound its wake cursor while the passive-ping checkpoint advanced: no notification turn ever
ran in the subscribed chat.
"""
import asyncio

import pytest

# Import-time update recovery stats the checkout's git dir; collection runs before the home I/O guard.
import gateway.session_managed_worker


@pytest.mark.asyncio
async def test_local_push_wake_is_committed_to_the_subscriber_fifo(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from gateway import run
    from gateway.config import GatewayConfig, Platform
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from gateway.session_local import create_local_session
    from gateway.session_local_recovery import local_adapter_map
    from gateway.wake import deliver_wake
    from hermes_state_runtime import list_session_admissions

    home = tmp_path / 'home'
    home.mkdir()
    (home / 'config.yaml').write_text(
        'platform_toolsets: {cli: []}\n'
        'model: {provider: custom, default: m, base_url: http://default.invalid/v1}\n')
    monkeypatch.setattr(run, '_hermes_home', home)
    monkeypatch.setenv('HERMES_HOME', str(home))  # a single-profile owner: this IS the launch home
    store = SessionStore(home / 'sessions', GatewayConfig())
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
                             _cached_agent_for=lambda route: None, _adapter_for_source=lambda source: None,
                             _evict_cached_agent=lambda route: None,
                             _session_key_for_source=lambda source: store._generate_session_key(source))
    owner = await initialize_session_authority(runner, profile_id=str(home), instance_id='fixture')
    connection = AuthorityConnection(owner, object(), {'user_id': 'owner'})
    ref = create_local_session(owner, connection.actor,
                               {'request_id': 'wake-repro', 'source': 'cli', 'cwd': str(home), 'toolsets': []})
    adapter = local_adapter_map(owner)[Platform.LOCAL]
    runner._adapter_for_source = lambda source: adapter
    live = owner.sessions[ref.session_id]
    # The FIFO commit, not the turn it schedules, is what this bug refused; park the drain.
    monkeypatch.setattr(owner, '_drain', lambda sid: asyncio.sleep(0))

    try:
        await deliver_wake(adapter, text='card needs input', source=live.source)

        rows = list_session_admissions(owner.db, session_id=ref.session_id, pending_only=False)
        assert [row['status'] for row in rows] == ['queued'], rows
        payload = rows[0]['payload']
        assert payload['text'] == 'card needs input'
        assert payload['local_automation_v1']['owner'] == ref.session_id
    finally:
        await connection.close()
        owner.db.close()
