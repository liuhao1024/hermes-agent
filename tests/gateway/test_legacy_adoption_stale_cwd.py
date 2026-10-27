"""A legacy row's saved cwd must not block its adoption when it is no longer a valid launch cwd.

Pre-authority desktop rows saved ``cwd='/'``, which a Windows Path reads as *relative*
(``Path('/').is_absolute()`` is ``False`` there), so ``build_policy`` refused the adoption with
``invalid_params`` on every open of the bot (#145332) — a saved value that was legal when written
became a permanent refusal the UI cannot clear. A directory deleted since refuses identically
everywhere. The launch params now apply build_policy's own bar before carrying a saved cwd, and a
row that fails it resumes on the profile's ``terminal.cwd``, exactly like a row without a cwd.
"""
from pathlib import Path, PosixPath
from types import SimpleNamespace

import pytest


@pytest.fixture
def adoption(tmp_path, monkeypatch):
    """A real authority over a temp home, with a native transport allowed to adopt legacy rows."""
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(run, '_load_gateway_config',
                        lambda: {'platform_toolsets': {'cli': []}, 'agent': {}})
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    db = store._db

    async def build():
        from gateway.session_authority import initialize_session_authority
        from gateway.session_contract import Principal
        runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False)
        authority = await initialize_session_authority(runner, profile_id=str(tmp_path), instance_id='first')
        caps = frozenset({'session:create', 'session:read', 'session:submit', 'session:control'})
        native = Principal('native', str(tmp_path), caps, 'native-socket')
        authority._native_legacy_transports = {native.transport_id: native}
        return authority, native

    return SimpleNamespace(tmp_path=tmp_path, db=db, build=build)


def _desktop_row(db, sid, title, cwd):
    db.create_session(sid, source='desktop', model='old-model', cwd=cwd)
    db.set_session_title(sid, title)
    db.append_message(sid, role='user', content='EARLIER')
    db.end_session(sid, 'cli_exit')


@pytest.mark.asyncio
async def test_a_since_deleted_saved_cwd_no_longer_blocks_adoption(adoption):
    import shutil

    from agent.runtime_cwd import resolve_agent_cwd
    from gateway.config import Platform
    from gateway.session_local_title import resolve_titled_session

    authority, native = await adoption.build()
    gone = adoption.tmp_path / 'workspace' / 'deleted-later'
    gone.mkdir(parents=True)
    _desktop_row(authority.db, 'old-desktop', 'My stale workspace', str(gone))
    shutil.rmtree(gone)

    ref = resolve_titled_session(authority, native, 'My stale workspace')
    assert ref.session_id == 'old-desktop'
    policy = authority.runner.adapters[Platform.LOCAL].policies['old-desktop']
    assert policy.cwd == str(resolve_agent_cwd())
    snapshot = await authority.attach(native, ref)
    assert [m['content'] for m in snapshot.history if not m.get('archived')] == ['EARLIER']


@pytest.mark.asyncio
async def test_a_still_valid_saved_cwd_still_carries_into_the_adoption(adoption):
    from gateway.config import Platform
    from gateway.session_local_title import resolve_titled_session

    authority, native = await adoption.build()
    workspace = adoption.tmp_path / 'workspace'
    workspace.mkdir()
    _desktop_row(authority.db, 'old-desktop', 'My kept workspace', str(workspace))

    ref = resolve_titled_session(authority, native, 'My kept workspace')
    assert ref.session_id == 'old-desktop'
    policy = authority.runner.adapters[Platform.LOCAL].policies['old-desktop']
    assert policy.cwd == str(workspace)


@pytest.mark.parametrize('saved', ['/', 'C:\\'])
def test_a_windows_relative_root_cwd_is_dropped(saved, monkeypatch):
    """On Windows ``Path('/').is_absolute()`` is False; the row resumes on the profile cwd, as if
    it never had one. The POSIX test above covers the ``is_dir`` half with a deleted directory."""
    import gateway.session_local_migration as mod

    class _WindowsSemantics(PosixPath):
        def is_absolute(self):
            return str(self) != '/'

    monkeypatch.setattr(mod, 'Path', _WindowsSemantics)
    params, _api_mode = mod.legacy_launch_params(
        {'id': 'old-desktop', 'source': 'desktop', 'cwd': saved, 'model': 'old-model'})
    assert 'cwd' not in params
    assert params['model'] == 'old-model'


def test_a_posix_root_cwd_still_carries_where_it_is_absolute_and_existing():
    from gateway.session_local_migration import legacy_launch_params

    params, _api_mode = legacy_launch_params(
        {'id': 'old-desktop', 'source': 'desktop', 'cwd': '/', 'model': 'old-model'})
    assert params['cwd'] == '/'
