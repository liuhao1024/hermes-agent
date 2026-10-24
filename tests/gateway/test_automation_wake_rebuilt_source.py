"""A wake rebuilt from the session key admits on its owning thread route (#144006).

``run_notifications._build_process_event_source``'s derived fallback can only fill the
fields the delegation ledger persisted (scope_id/user_id/user_name plus the key-parsed
platform/chat_id/chat_type/profile). A Discord thread's committed source additionally
carries ``parent_chat_id`` and ``chat_name``, so a plain identity compare refused every
completion wake on such threads forever. Missing on one side is unknown, not drift.
"""
from types import SimpleNamespace

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.event import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from gateway.session_authority import SessionAuthority, initialize_session_authority
from hermes_state_runtime import RuntimeStoreError, get_session_admission, list_session_admissions


async def _authority(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    monkeypatch.setattr('gateway.session_bot._watch_reply', lambda *args: None)
    runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / 'sessions'))
    from hermes_state import SessionDB
    db = SessionDB(tmp_path / 'state.db')
    runner.session_store._db = db
    runner._session_db = db
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='wake-fixture',
                                                  db=db)
    return runner, authority


# A Discord thread as the adapter commits it: chat_id is the thread id, parent_chat_id names
# the owning channel, thread_id stays unset so the session key matches the derived fallback.
_THREAD_SOURCE = dict(chat_id='100', chat_type='thread', parent_chat_id='90',
                      scope_id='80', user_id='42', user_name='human')


async def _thread_owner(tmp_path, monkeypatch):
    from gateway.session_ingress_context import native_callback, register_transport_home
    from plugins.platforms.discord.adapter import DiscordAdapter
    runner, authority = await _authority(tmp_path, monkeypatch)
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='***', typing_indicator=False))
    runner.adapters = {Platform.DISCORD: adapter}
    runner._wire_adapter_handlers(adapter)
    register_transport_home(runner, None, tmp_path)
    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    source = adapter.build_source(**_THREAD_SOURCE)
    human = MessageEvent(text='human', source=adapter.build_source(**_THREAD_SOURCE, message_id='human-1'),
                         message_id='human-1')
    with native_callback(runner, human, tmp_path):
        await authority.admit_native(human)
    route = runner.session_store._generate_session_key(source)
    metadata = {'gateway_session_key': route, 'gateway_session_id': runner.session_store.peek_session_id(route)}
    return runner, authority, adapter, source, metadata


def _derived_wake_source(**overrides) -> SessionSource:
    """The source the derived fallback builds: only ledger-persisted fields survive."""
    fields = dict(chat_id='100', chat_type='thread', user_id='42', user_name='human', scope_id='80')
    fields.update(overrides)
    return SessionSource(platform=Platform.DISCORD, **fields)


@pytest.mark.asyncio
async def test_derived_thread_wake_admits_without_parent_chat_id(tmp_path, monkeypatch):
    """The rebuilt wake never carries parent_chat_id/chat_name; admission must still match."""
    runner, authority, adapter, _source, metadata = await _thread_owner(tmp_path, monkeypatch)
    try:
        from gateway.session_automation import committed_route_envelope
        entry = runner.session_store._entries[metadata['gateway_session_key']]
        committed = committed_route_envelope(authority, entry)
        assert committed is not None and 'parent_chat_id' in committed['source']
        event = MessageEvent(text='notice', source=_derived_wake_source(), internal=True,
                             metadata=dict(metadata))
        receipt = await authority.admit_automation(adapter, event, 'notice')
        row = get_session_admission(authority.db, admission_id=receipt.admission_id)
        envelope = row['payload']['native_text_v1']
        # The wake borrows the committed route's source and proof, not its own sparser one.
        assert envelope['source'] == {k: v for k, v in committed['source'].items() if k != 'message_id'}
        assert envelope['provenance'] == committed['provenance']
        assert envelope['route'] == metadata['gateway_session_key']
        assert envelope['automation']['owner'] == authority.logical_owner(metadata['gateway_session_id'])
        # The same immutable completion replays onto the same admission.
        again = await authority.admit_automation(adapter, event, 'notice')
        assert again.admission_id == receipt.admission_id
    finally:
        authority.db.close()


@pytest.mark.asyncio
async def test_derived_thread_wake_still_refuses_parent_chat_id_drift(tmp_path, monkeypatch):
    """Both sides carrying a parent_chat_id that differs is real drift and still refuses."""
    _runner, authority, adapter, _source, metadata = await _thread_owner(tmp_path, monkeypatch)
    try:
        event = MessageEvent(text='notice', source=_derived_wake_source(parent_chat_id='7'),
                             internal=True, metadata=dict(metadata))
        with pytest.raises(RuntimeStoreError, match='admission_conflict'):
            await authority.admit_automation(adapter, event, 'notice')
        owner = authority.logical_owner(metadata['gateway_session_id'])
        assert not [r for r in list_session_admissions(authority.db, session_id=owner, pending_only=False)
                    if (r['payload'].get('native_text_v1') or {}).get('automation')]
    finally:
        authority.db.close()


@pytest.mark.asyncio
async def test_derived_thread_wake_still_refuses_scope_drift(tmp_path, monkeypatch):
    """A scope set on both sides but pointing at another guild must never admit."""
    _runner, authority, adapter, _source, metadata = await _thread_owner(tmp_path, monkeypatch)
    try:
        event = MessageEvent(text='notice', source=_derived_wake_source(scope_id='81'),
                             internal=True, metadata=dict(metadata))
        with pytest.raises(RuntimeStoreError, match='admission_conflict'):
            await authority.admit_automation(adapter, event, 'notice')
    finally:
        authority.db.close()
