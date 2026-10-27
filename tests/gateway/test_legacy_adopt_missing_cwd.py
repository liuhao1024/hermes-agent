"""A legacy row whose stored working directory is gone still adopts (#145471).

``adopt_legacy_session`` re-creates the row as a launch, and ``build_policy`` refuses any
``cwd`` that is not an existing absolute directory. A session started inside a git worktree
that a later ``git worktree remove`` deleted used to wedge its intact transcript behind
``invalid_params field=cwd`` on every resume. The adoption now lands on the nearest ancestor
that still exists, else the process's own directory.

Real: SessionStore/SessionAuthority/AuthorityConnection, ``resolve_local_target`` ->
``adopt_legacy_session`` -> ``build_policy``. Seam: a SimpleNamespace runner. No endpoint is
contacted.
"""
from pathlib import Path

import pytest

from tests.gateway.test_session_follow_profile_config import _owner


@pytest.fixture
def home(monkeypatch):
    """The profile home the adoption tests write ``config.yaml`` into."""
    from gateway import run
    from hermes_constants import get_hermes_home
    profile = get_hermes_home()
    profile.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run, '_hermes_home', profile)
    return profile


def _legacy_row(db, cwd, sid='old-tui'):
    db.create_session(sid, cwd=str(cwd), source='tui', model='saved-model')
    db.end_session(sid, 'tui_close')


def _adopted_policy_cwd(authority, owner, sid):
    from gateway.session_local_migration import resolve_local_target
    from hermes_state_local import local_receipt
    ref = resolve_local_target(authority, owner.actor, sid)
    return local_receipt(authority.db, ref.session_id)['policy']['cwd']


@pytest.mark.asyncio
async def test_a_legacy_row_whose_worktree_is_gone_adopts_on_the_nearest_existing_ancestor(home):
    (home / 'config.yaml').write_text('{}\n')
    store, runner, authority, owner = _owner(home)
    runner._peek_session_state = lambda key: None
    gone = home / 'worktrees' / 'wt-145471'  # never created: the worktree was removed
    _legacy_row(authority.db, gone)
    authority._native_legacy_transports = {owner.actor.transport_id: owner.actor}
    try:
        assert _adopted_policy_cwd(authority, owner, 'old-tui') == str(home)
    finally:
        await owner.close()
        store.close_all_db_handles()


@pytest.mark.asyncio
async def test_a_legacy_row_with_no_existing_ancestor_adopts_on_the_process_cwd(home, monkeypatch):
    (home / 'config.yaml').write_text('{}\n')
    store, runner, authority, owner = _owner(home)
    runner._peek_session_state = lambda key: None
    _legacy_row(authority.db, '/nonexistent-hermes-145471/leaf')  # no existing ancestor short of '/'
    monkeypatch.setattr('agent.runtime_cwd.resolve_agent_cwd', lambda: home)
    authority._native_legacy_transports = {owner.actor.transport_id: owner.actor}
    try:
        assert _adopted_policy_cwd(authority, owner, 'old-tui') == str(home)
    finally:
        await owner.close()
        store.close_all_db_handles()
