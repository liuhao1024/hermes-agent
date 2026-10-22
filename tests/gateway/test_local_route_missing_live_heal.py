"""Regression for #142844: a live local session whose route entry the loaded store cannot see
(a legacy ``''`` scope, a pruned row) heals from the receipt, instead of wedging every later
claim on ``admission_conflict`` until a gateway restart. A route that names a different,
non-stray session must keep refusing."""
import pytest

from tests.gateway.test_session_authorities_multiplex import _reserve_homes, _runner


@pytest.mark.asyncio
async def test_live_session_heals_a_route_missing_from_the_loaded_store(tmp_path, monkeypatch):
    from gateway import run
    from gateway.run import _profile_runtime_scope
    from gateway.run_runtime import initialize_gateway_runtime
    from gateway.runtime_ownership import process_ownership
    from gateway.session_contract import Principal
    from gateway.session_local import create_local_session
    from gateway.session_local_recovery import restore_local_session
    from hermes_state_local import local_receipt

    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'platform_toolsets': {'cli': []}})
    root, homes = _reserve_homes(tmp_path, monkeypatch, names=('alpha',))
    process_ownership.reserve([home for _, home in homes])
    runners = []
    try:
        first = _runner(root, homes)
        runners.append(first)
        await initialize_gateway_runtime(first)
        home = homes[1][1]
        authority = first.session_authorities.for_home(home)
        actor = Principal('owner', authority.profile_id,
                          frozenset({'session:create', 'session:read', 'session:control'}), 'socket')
        with _profile_runtime_scope(home, {}):
            ref = create_local_session(authority, actor, {'request_id': 'missing-route', 'source': 'gui',
                'cwd': str(tmp_path), 'model': 'frozen', 'toolsets': []})
        assert ref.session_id in authority.sessions  # live: the drain re-enters restore_local_session
        receipt = local_receipt(authority.db, ref.session_id)
        route = authority.sessions[ref.session_id].route
        store = authority.runner.session_store
        with store._lock:
            # The loaded store cannot see the row: a scope it never reads, or one the prune retired.
            store._entries.pop(route, None)
        assert store._entry_locked(route) is None

        # The live branch recreates the route's entry from the receipt instead of refusing.
        with _profile_runtime_scope(home, {}):
            healed = restore_local_session(authority, ref.session_id)
        assert healed == ref
        with store._lock:
            assert store._entries[route].session_id == receipt['entry']['session_id']

        # A route naming a different, non-stray session (one with admissions of its own) still refuses.
        with _profile_runtime_scope(home, {}):
            other = create_local_session(authority, actor, {'request_id': 'busy-rival', 'source': 'gui',
                'cwd': str(tmp_path), 'model': 'frozen', 'toolsets': []})
        from hermes_state_runtime import admit_session_input
        admit_session_input(authority.db, epoch=authority.epoch, principal_id=actor.subject,
                            session_id=other.session_id, request_id='rival-input', payload={'input': 'x'})
        with store._lock:
            store._entries[route].session_id = other.session_id
        from hermes_state_runtime import RuntimeStoreError
        with _profile_runtime_scope(home, {}):
            with pytest.raises(RuntimeStoreError):
                restore_local_session(authority, ref.session_id)
    finally:
        for runner in runners:
            for owner in getattr(runner, 'session_authorities', ()):
                owner.db.close()
        for _, home in homes:
            process_ownership.release(home)
