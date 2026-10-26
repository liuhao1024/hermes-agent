"""The cron run scope follows the authority's profile home, not the symlinked store directory.

``execute()`` used to derive the runtime scope from ``db.db_path`` via
``.resolve().parent``; a profile whose state.db lives on a symlinked volume then
ran every fire inside the store's directory — no skills/, no .env, no provider
credentials (issue #145075).
"""
from types import SimpleNamespace

import pytest

# Imported at module top level, like the rest of tests/gateway: the first import of
# gateway.run settles the checkout's update state, and that must happen before the
# real-home I/O guard arms (it would refuse the update-marker probe).
from cron import scheduler
from gateway import run, session_cron


@pytest.mark.asyncio
async def test_execute_runs_inside_the_profile_home_scope_past_a_symlinked_store(tmp_path, monkeypatch):
    from contextlib import nullcontext
    import json
    from tests.gateway.test_session_cron_symlink_store import _symlinked_store_authority

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    authority, db, home, store_dir = _symlinked_store_authority(tmp_path, monkeypatch)
    try:
        authority.sessions['s'] = SimpleNamespace(
            source=SimpleNamespace(user_id='cron-owner'))
        scoped = {}

        def scope(home_arg):
            scoped['home'] = home_arg
            return nullcontext()
        monkeypatch.setattr(run, '_profile_runtime_scope', scope)
        monkeypatch.setattr(scheduler, 'run_job', lambda job, **kw: (True, 'doc', 'answer', None))
        request_id = 'cron-fire-1'
        row = {'admission_id': 'adm1', 'request_id': request_id, 'payload': {'text': ''},
               'principal_id': 'cron-owner'}
        policy = SimpleNamespace(request_json=json.dumps({
            'extra_prompt': None, 'request_id': request_id, 'cron_job': {'id': 'job', 'name': 'Job'}}))
        ref = SimpleNamespace(session_id='s')
        run_fire = getattr(session_cron, 'execute')
        assert await run_fire(authority, ref, row, policy) == 'answer'
    finally:
        db.close()
    assert scoped['home'] == home
