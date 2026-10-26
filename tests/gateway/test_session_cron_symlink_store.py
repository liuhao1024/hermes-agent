"""A store reached through a symlink must not move the cron owner or the run scope.

The profile home is the authority's identity (``profile_id``); deriving it from
``db.db_path`` via ``.resolve().parent`` sends every cron run of a profile whose
state.db lives on a symlinked volume into the store's directory instead — no
skills/, no .env, no credentials (issue #145075).
"""
from types import SimpleNamespace

import pytest


def _symlinked_store_authority(tmp_path, monkeypatch):
    """An authority whose profile home holds a state.db symlinked to a store kept in a sibling
    directory (an external volume in production). Returns (authority, db, home, store_dir)."""
    from gateway.session_authority import SessionAuthority
    from hermes_state import SessionDB
    from hermes_state_runtime import begin_runtime_epoch

    home, store_dir = tmp_path / 'profile', tmp_path / 'db-store'
    store_dir.mkdir()
    SessionDB(store_dir / 'state.db').close()
    home.mkdir()
    (home / 'state.db').symlink_to(store_dir / 'state.db')
    db = SessionDB(home / 'state.db')
    try:
        authority = SessionAuthority(SimpleNamespace(_draining=False), profile_id=str(home),
                                     instance_id='test', db=db,
                                     epoch=begin_runtime_epoch(db, instance_id='test'))
        return authority, db, home, store_dir
    except BaseException:
        db.close()
        raise


@pytest.mark.asyncio
async def test_owner_key_and_delivery_follow_the_profile_home_past_a_symlinked_store(tmp_path, monkeypatch):
    from gateway import session_cron

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    authority, db, home, store_dir = _symlinked_store_authority(tmp_path, monkeypatch)
    try:
        session_cron.bind_owner(authority)
        # The owner answers for the profile home, not for wherever the store file physically lives.
        assert session_cron.owner_for_home(home) is not None
        assert session_cron.owner_for_home(store_dir) is None
        session_cron.unbind_owner(authority)
        assert session_cron.owner_for_home(home) is None
    finally:
        db.close()


def test_single_authority_lookup_answers_by_profile_id_past_a_symlinked_store(tmp_path, monkeypatch):
    from gateway.session_authorities import authority_for_home, authority_for_profile_id

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    authority, db, home, store_dir = _symlinked_store_authority(tmp_path, monkeypatch)
    try:
        runner = SimpleNamespace(session_authorities=None, session_authority=authority)
        assert authority_for_home(runner, home) is authority
        assert authority_for_profile_id(runner, str(home)) is authority
        assert authority_for_home(runner, store_dir) is None
    finally:
        db.close()
