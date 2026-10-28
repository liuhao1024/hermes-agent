"""The claim/verify legs of worker_request must not run on the gateway event loop.

A synchronous psutil probe or SQLite read inside _claim/_verify stalls the loop while
several workers save at once; the worker misses its 20 s worker.persist reply window and
exits, which is the "Kanban execution unknown; retained by owner" crash (#145022).
"""

import asyncio
import threading
import time

import pytest

from gateway import session_authorities
from gateway import session_worker


class _FakeActor:
    profile_id = "profile"
    subject = "worker-subject"
    capabilities = frozenset({"worker:adopt"})


class _FakeAuthority:
    profile_id = "profile"
    epoch = 7
    db = object()

    def _require_admission_open(self):
        pass

    def authorize(self, actor, ref, capability):
        pass


class _FakeConnection:
    authority = _FakeAuthority()
    actor = _FakeActor()


def _params(operation):
    params = {key: "x" for key in session_worker._SCOPE}
    params.update(
        profile_id="profile",
        session_id="sid",
        execution_id="exec",
        generation=1,
        pid=1,
        birth=1,
        secret="s" * 32,
    )
    extra = {
        "register": {"kind": "compute"},
        "adopt": {},
        "persist": {
            "epoch": 1,
            "sequence": 1,
            "operation": "execution.finish",
            "payload": {},
        },
    }
    params.update(extra[operation])
    return params


@pytest.mark.asyncio
async def test_claim_and_verify_run_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    seen = {}
    real_sleep = time.sleep

    def slow(name, seconds=0.05):
        def recorded(*args, **kwargs):
            seen[name] = threading.get_ident()
            real_sleep(seconds)
            return f"{name}-result"

        return recorded

    monkeypatch.setattr(session_worker, "_claim", slow("claim"))
    monkeypatch.setattr(session_worker, "_verify", slow("verify"))
    monkeypatch.setattr(
        session_worker, "register_worker_execution", lambda *a, **k: "registered"
    )
    monkeypatch.setattr(
        session_worker, "adopt_worker_execution", lambda *a, **k: "adopted"
    )
    monkeypatch.setattr(session_authorities, "require_serving", lambda authority: None)

    connection = _FakeConnection()

    async def drive(operation):
        return await session_worker.worker_request(
            connection, "ref", _params(operation), operation=operation
        )

    register_result, adopt_result = await asyncio.gather(
        drive("register"), drive("adopt")
    )
    assert register_result == "registered"
    assert adopt_result == "adopted"
    assert seen["claim"] != loop_thread, "_claim blocked the event loop"
    assert seen["verify"] != loop_thread, "_verify blocked the event loop"
