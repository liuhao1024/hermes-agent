"""The background review fork must not leave its throwaway session id in os.environ.

Regression coverage for issue #142720: constructing the fork publishes its fresh
session id process-wide (``AIAgent.__init__`` → ``_init_session_state`` →
``_publish_session_id`` → ``set_current_session_id``, which — outside a
delegated-child context — also writes ``os.environ["HERMES_SESSION_ID"]``). The
fork never persists that id (``_persist_disabled``, ``_session_db = None``), so
after the first background review every tool reading the session id through the
``os.environ`` fallback — and every terminal subprocess inheriting it — resolves
an id with no ``state.db`` row.
"""

import os
from unittest.mock import patch

from tests.agent.test_background_review_cache_parity import (
    _SyncThread,
    _make_agent_stub,
    _make_recorder_class,
)


class _PublishingRecorder(_make_recorder_class()):
    """Recorder that reproduces the real constructor's session-id publication.

    ``AIAgent.__init__`` finishes with ``_init_session_state``, whose
    ``_publish_session_id`` call routes through the real
    ``set_current_session_id`` and (outside a delegated-child context) writes
    ``os.environ["HERMES_SESSION_ID"]``. Only that publication is reproduced
    here; everything else mirrors the cache-parity recorder stubs.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from gateway.session_context import set_current_session_id

        self.session_id = "fork-throwaway-session"
        set_current_session_id(self.session_id)


def _spawn_inline(agent):
    import run_agent

    with (
        patch.object(run_agent, "AIAgent", _PublishingRecorder),
        patch("threading.Thread", _SyncThread),
    ):
        agent._spawn_background_review(
            messages_snapshot=[],
            review_memory=True,
            review_skills=False,
        )


def test_fork_does_not_leave_its_session_id_in_environ(monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_ID", "parent-live-session")
    import run_agent

    agent = _make_agent_stub(run_agent.AIAgent)
    _spawn_inline(agent)

    assert os.environ.get("HERMES_SESSION_ID") == "parent-live-session", (
        "The background review fork's construction leaked its throwaway session "
        'id into os.environ["HERMES_SESSION_ID"] — tools and terminal '
        "subprocesses afterwards resolve an id that has no state.db row (#142720)."
    )


def test_fork_does_not_create_environ_session_id_when_absent(monkeypatch):
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    import run_agent

    agent = _make_agent_stub(run_agent.AIAgent)
    _spawn_inline(agent)

    assert "HERMES_SESSION_ID" not in os.environ, (
        "The background review fork's construction created "
        'os.environ["HERMES_SESSION_ID"] although no session id was published '
        "before the fork ran (#142720)."
    )
