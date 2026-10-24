"""Legacy adoption of custom surface labels (#143831).

Older builds stamped surface names freely (``hermes chat --source <name>``, custom cron surfaces),
so rows with a ``source`` outside ``LEGACY_LOCAL_SOURCES`` could still be pure unrouted local
history. The adoption whitelist refused them for every caller, which made such sessions
permanently unresumable after the 0.22 upgrade: their pinned Desktop chats answered ``not_found``
by id and by title. A custom label no platform answers to now adopts like the known local labels —
launching as an unattended CLI run and keeping its stored label — while routed domains (messaging
platforms, the API transport), bound rows, foreign owners and blank labels stay refused.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.session_contract import Principal, SessionRef
from gateway.session_controls import AuthorityConnection
from hermes_state import SessionDB
from hermes_state_local_migration import custom_surface_label, validate_legacy_row
from hermes_state_runtime import RuntimeStoreError


def test_custom_surface_label_answers_only_for_unknown_local_names():
    assert custom_surface_label('garam') is True  # a surface name an older build stamped freely
    for known in ('cli', 'tui', 'gui', 'desktop', 'oneshot', 'tool', 'acp'):
        assert custom_surface_label(known) is False
    for routed in ('telegram', 'discord', 'whatsapp', 'api_server', 'local'):
        assert custom_surface_label(routed) is False  # a routed domain or canonical label
    # Labels the authority era writes itself: their rows are never pre-authority leftovers.
    for era in ('cron', 'kanban', 'bot_peer_dm', 'dashboard', 'hermes_browser', 'unknown'):
        assert custom_surface_label(era) is False
    assert custom_surface_label('') is False
    assert custom_surface_label(None) is False


def test_validate_adopts_an_unrouted_custom_label_row(tmp_path):
    db = SessionDB(db_path=tmp_path / 'state.db')
    try:
        db.create_session('garam-row', source='garam')
        db.create_session('garam-bound', source='garam', session_key='agent:main:telegram:dm:42')
        db.create_session('garam-foreign', source='garam', user_id='basic:alice')
        db.create_session('telegram-row', source='telegram')
        db.create_session('api-row', source='api_server')
        # The Bot Chat an older gateway minted through the plain API create (#140769): an
        # unrouted ``bot_peer_dm`` row no native client may ever adopt.
        db.create_session('peer-dm-row', source='bot_peer_dm')
        # The custom label adopts exactly like a pre-authority ``tui`` row: unrouted, unstamped,
        # own profile. Bound identity, a foreign owner and routed domains stay refused.
        validate_legacy_row(db, db.get_session('garam-row'))
        with pytest.raises(RuntimeStoreError, match='not_found'):
            validate_legacy_row(db, db.get_session('garam-bound'))
        with pytest.raises(RuntimeStoreError, match='not_found'):
            validate_legacy_row(db, db.get_session('garam-foreign'))
        for sid in ('telegram-row', 'api-row', 'peer-dm-row'):
            with pytest.raises(RuntimeStoreError, match='not_found'):
                validate_legacy_row(db, db.get_session(sid))
    finally:
        db.close()


@pytest.mark.asyncio
async def test_adopted_custom_label_launches_as_cli_and_keeps_its_stored_label(tmp_path, monkeypatch):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_local_migration import bind_native_transport, resolve_local_target
    from hermes_state_local import local_receipt

    monkeypatch.setattr(run, '_load_gateway_config', lambda: {
        'model': {'default': 'profile-model', 'provider': 'openrouter',
                  'base_url': 'https://openrouter.ai/api/v1', 'api_mode': 'chat_completions'},
        'platform_toolsets': {'cli': []}})
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
                             _cached_agent_for=lambda route: None, _adapter_for_source=lambda source: None)
    authority = await initialize_session_authority(runner, profile_id=str(tmp_path), instance_id='fixture')
    actor = Principal('uid:1000', str(tmp_path), frozenset({'session:read', 'session:create'}), 'native')
    bind_native_transport(authority, actor, {'provider': 'local', 'profile_id': str(tmp_path),
                                             'instance_id': 'fixture'})
    # The reported row: a custom cron surface's label, ~1800 messages, no binding at all.
    authority.db.create_session('20260820_072203_1ba47b', source='garam', model='saved-model')
    try:
        ref = resolve_local_target(authority, actor, '20260820_072203_1ba47b')
        policy = local_receipt(authority.db, '20260820_072203_1ba47b')['policy']
        assert policy['source'] == 'cli' and policy['model'] == 'saved-model'
        # Binding adopted the row in place: the stored label stays as provenance.
        assert authority.db.get_session('20260820_072203_1ba47b')['source'] == 'garam'
        # A second resume is an ordinary bound lookup, never a re-adoption.
        assert resolve_local_target(authority, actor, '20260820_072203_1ba47b') is not None
        assert ref.session_id == '20260820_072203_1ba47b'
    finally:
        store._db.close()


def _attach_snapshot(owner_ref):
    return SimpleNamespace(
        subscription_id='sub',
        history=(),
        pending=(),
        prompts=(),
        replay_epoch='e',
        last_sequence=0,
        handle=SimpleNamespace(
            ref=owner_ref,
            execution_state='idle',
            authority_epoch=1,
            revision=1,
            execution_generation=0,
        ),
    )


@pytest.mark.asyncio
async def test_session_resume_routes_a_custom_label_row_through_adoption(monkeypatch):
    # The routing table itself: a custom label row reaches ``resolve_local_target`` (adoption),
    # the branch pre-authority cli/tui/gui rows take. A routed label never does —
    # ``custom_surface_label('telegram')`` is False — so it keeps the messaging arm unchanged.
    owner_id = 'legacy-owner'
    tip_id = 'legacy-tip'
    owner_ref = SessionRef('fixture', owner_id)
    resolved = []

    monkeypatch.setattr(
        'gateway.session_local_migration.resolve_local_target',
        lambda authority, actor, sid: resolved.append(sid) or owner_ref)

    authority = SimpleNamespace(
        profile_id='fixture',
        instance_id='one',
        events={},
        db=SimpleNamespace(
            get_session=lambda sid: {
                'id': tip_id, 'source': 'garam', 'chat_id': owner_id,
            } if sid == tip_id else None,
            get_compression_tip=lambda sid: tip_id,
        ),
        sessions={owner_id: SimpleNamespace(source=SimpleNamespace(platform=None), route=f'agent:main:cli:dm:{owner_id}')},
        authorize=lambda *a, **k: None,
        attach=AsyncMock(return_value=_attach_snapshot(owner_ref)),
    )
    connection = AuthorityConnection(
        authority, object(),
        {'user_id': 'owner', 'capabilities': [
            'session:create', 'session:read', 'session:control']},
    )
    await connection.resume(SessionRef('fixture', tip_id), {})
    assert resolved == [tip_id]
