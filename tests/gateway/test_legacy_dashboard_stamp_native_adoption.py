"""A host's own dashboard login stamp still adopts for the redeemed native ticket (#144814).

A dashboard-login client (hermes-mobile) mints rows through the WS upgrade, so its chats
land stamped ``basic:admin``: unrouted local history in every other column, but the stamp
is neither the Desktop native subject nor a login label, so ``session.resume`` answered
``not_found`` forever. Every caller that reaches ``adopt_legacy_session`` has already
passed its redeemed same-profile native gate — a remote login never gets that far — so
there the stamp must not double as a wall against the host's own primary client. Other
``validate_legacy_row`` callers (stored listing, bot chat, receipt rebinding) keep the
stamp as their boundary: their callers are not pre-cleared.
"""
import json
from types import SimpleNamespace

import pytest


def _mint_phone_row(db, tmp_path, stamp):
    """A row exactly as hermes-mobile mints it: legacy surface, no route fields, login stamp."""
    db.create_session('phone-chat', source='desktop', model='old-model', cwd=str(tmp_path), user_id=stamp)
    db.append_message('phone-chat', role='user', content='PHONE_QUESTION')
    db.append_message('phone-chat', role='assistant', content='PHONE_ANSWER')
    db.end_session('phone-chat', 'cli_exit')


async def _authority(tmp_path, monkeypatch):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    config = {'platform_toolsets': {'cli': []},
              'dashboard': {'basic_auth': {'username': 'admin'}}}
    monkeypatch.setattr(run, '_load_gateway_config', lambda: config)
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False)
    return store._db, await initialize_session_authority(runner, profile_id=str(tmp_path), instance_id='first')


CAPS = frozenset({'session:create', 'session:read', 'session:submit', 'session:control'})


@pytest.mark.asyncio
async def test_dashboard_stamp_adopts_for_redeemed_native_ticket(tmp_path, monkeypatch):
    from gateway.session_contract import Principal
    from gateway.session_local_title import resolve_titled_session
    from hermes_state_local import local_receipt

    db, authority = await _authority(tmp_path, monkeypatch)
    _mint_phone_row(db, tmp_path, 'basic:admin')
    native = Principal('native', str(tmp_path), CAPS, 'native-socket')
    authority._native_legacy_transports = {native.transport_id: native}

    ref = resolve_titled_session(authority, native, 'phone-chat')
    assert ref.session_id == 'phone-chat'
    snapshot = await authority.attach(native, ref)
    assert [m['content'] for m in snapshot.history] == ['PHONE_QUESTION', 'PHONE_ANSWER']
    receipt = local_receipt(db, ref.session_id)
    assert receipt['entry']['session_id'] == 'phone-chat'


@pytest.mark.asyncio
async def test_dashboard_stamp_stays_foreign_without_native_redemption(tmp_path, monkeypatch):
    from gateway.session_contract import Principal
    from gateway.session_local_title import resolve_titled_session
    from hermes_state_local_migration import validate_legacy_row
    from hermes_state_runtime import RuntimeStoreError

    db, authority = await _authority(tmp_path, monkeypatch)
    _mint_phone_row(db, tmp_path, 'basic:admin')
    remote = Principal('remote', str(tmp_path), CAPS, 'remote-socket')

    with pytest.raises(RuntimeStoreError, match='not_found'):
        resolve_titled_session(authority, remote, 'phone-chat')
    # Stored listing / bot chat keep the stamp as their boundary: their caller is not pre-cleared.
    with pytest.raises(RuntimeStoreError, match='not_found'):
        validate_legacy_row(authority.db, db.get_session('phone-chat'), subject=remote.subject)
    row = db.get_session('phone-chat')
    assert not row['session_key'] and not row['chat_id'] and not row['origin_json']


@pytest.mark.asyncio
async def test_other_dashboard_stamps_and_local_stamps_stay_foreign_to_native(tmp_path, monkeypatch):
    """RT-32 keeps its bite: only the host's own configured dashboard login joins the native
    ticket's adoptable stamps — another login name, another local user and an issuer-bearing
    subject stamp all stay refused for the very same native caller."""
    from gateway.session_contract import Principal
    from gateway.session_local_title import resolve_titled_session
    from hermes_state_runtime import RuntimeStoreError

    db, authority = await _authority(tmp_path, monkeypatch)
    native = Principal('native', str(tmp_path), CAPS, 'native-socket')
    authority._native_legacy_transports = {native.transport_id: native}
    for sid, stamp in (('alice-chat', 'basic:alice'), ('local-chat', 'uid:4242'),
                       ('sso-chat', 'auth:v1:["dashboard","","alice"]')):
        db.create_session(sid, source='desktop', model='old', cwd=str(tmp_path), user_id=stamp)
        with pytest.raises(RuntimeStoreError, match='not_found'):
            resolve_titled_session(authority, native, sid)


@pytest.mark.asyncio
async def test_dashboard_stamp_exact_login_and_foreign_login_semantics_unchanged(tmp_path, monkeypatch):
    from gateway.session_contract import Principal
    from hermes_state_local_migration import validate_legacy_row
    from hermes_state_runtime import RuntimeStoreError

    db, authority = await _authority(tmp_path, monkeypatch)
    _mint_phone_row(db, tmp_path, 'basic:admin')
    row = db.get_session('phone-chat')
    own_login = 'auth:v1:' + json.dumps(['basic', '', 'admin'], separators=(',', ':'))
    other_login = 'auth:v1:' + json.dumps(['basic', '', 'alice'], separators=(',', ':'))

    # The login that stamped it still adopts through the existing exact-match arm.
    validate_legacy_row(authority.db, row, subject=own_login)
    # A different login still does not (the native arm is adopt_legacy_session's to pass, and its
    # caller has already cleared the native gate, so no validate-level pair covers it here).
    with pytest.raises(RuntimeStoreError, match='not_found'):
        validate_legacy_row(authority.db, row, subject=other_login)
