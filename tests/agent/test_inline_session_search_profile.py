"""The inline ``session_search`` executor forwards ``profile`` so a routed gateway agent can read a
named profile's store instead of silently searching the injected (default) DB (#82903).

The gateway hard-injects ``db=agent._get_session_db_for_recall()``; without ``profile`` reaching
``tools.session_search_tool.session_search`` the tool's ``_resolve_profile_db`` never runs and
``profile="llm-wiki"`` returns the same rows as no profile at all.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS, InlineToolContext
from hermes_state import SessionDB


def test_session_search_honours_requested_profile_db(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    profile_home = hermes_home / "profiles" / "llm-wiki"
    profile_home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    current_db = SessionDB(hermes_home / "state.db")
    current_db.create_session("default-session", source="gateway")
    current_db.append_message("default-session", role="user", content="weekly report default")
    current_db._conn.commit()
    profile_db = SessionDB(profile_home / "state.db")
    profile_db.create_session("profile-session", source="cli")
    profile_db.append_message("profile-session", role="user", content="weekly report llm wiki")
    profile_db._conn.commit()
    profile_db.close()

    agent = SimpleNamespace(_get_session_db_for_recall=lambda: current_db, session_id="gw-1")
    ctx = InlineToolContext(effective_task_id="task-1", tool_call_id="call-1")
    try:
        routed = json.loads(INLINE_TOOL_EXECUTORS["session_search"](
            agent, {"query": "weekly report", "profile": "llm-wiki"}, ctx))
        missing = json.loads(INLINE_TOOL_EXECUTORS["session_search"](
            agent, {"query": "weekly report", "profile": "missing-profile"}, ctx))
    finally:
        current_db.close()

    assert [r["session_id"] for r in routed["results"]] == ["profile-session"]
    assert missing["success"] is False and "default-session" not in json.dumps(missing)


def test_session_search_runtime_store_falls_back_to_readonly_state_db(tmp_path, monkeypatch):
    """A managed worker's ``_get_session_db_for_recall`` hands out the RuntimeSessionStore RPC
    facade, which has none of the read interfaces ``_discover`` uses; the executor must drop it
    so the tool searches the read-only state DB instead of failing on AttributeError (#141705)."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    state_db = SessionDB(hermes_home / "state.db")
    state_db.create_session("worker-session", source="gateway")
    state_db.append_message("worker-session", role="user", content="quarterly numbers kanban")
    state_db._conn.commit()
    state_db.close()

    rpc_facade = SimpleNamespace()  # like RuntimeSessionStore: no search_messages et al.
    agent = SimpleNamespace(_get_session_db_for_recall=lambda: rpc_facade, session_id="worker-1")
    ctx = InlineToolContext(effective_task_id="task-1", tool_call_id="call-1")
    result = json.loads(INLINE_TOOL_EXECUTORS["session_search"](
        agent, {"query": "quarterly numbers"}, ctx))

    assert "search_messages" not in json.dumps(result)
    assert [r["session_id"] for r in result["results"]] == ["worker-session"]
