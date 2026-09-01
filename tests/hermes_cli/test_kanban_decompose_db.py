"""Tests for kb.decompose_triage_task — the DB-layer atomic fan-out
from the triage column. LLM-free by design.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _create_triage(conn, title="rough idea", body=None, assignee=None, tenant=None):
    return kb.create_task(
        conn,
        title=title,
        body=body,
        assignee=assignee,
        tenant=tenant,
        triage=True,
    )


def test_decompose_creates_children_and_promotes_root(kanban_home):
    with kb.connect() as conn:
        tid = _create_triage(conn, title="ship a feature")
        assert kb.get_task(conn, tid).status == "triage"

    children = [
        {"title": "research", "body": "look at prior art", "assignee": "researcher", "parents": []},
        {"title": "build it", "body": "write code", "assignee": "engineer", "parents": [0]},
    ]
    with kb.connect() as conn:
        child_ids = kb.decompose_triage_task(
            conn,
            tid,
            root_assignee="orchestrator",
            children=children,
            author="decomposer",
        )
    assert child_ids is not None
    assert len(child_ids) == 2

    with kb.connect() as conn:
        root = kb.get_task(conn, tid)
        c0 = kb.get_task(conn, child_ids[0])
        c1 = kb.get_task(conn, child_ids[1])

    # Root flipped to todo with orchestrator assignee, gated by children.
    assert root.status == "todo"
    assert root.assignee == "orchestrator"
    # First child has no internal parents → ready on recompute_ready.
    assert c0.status == "ready"
    assert c0.assignee == "researcher"
    # Second child has parents=[0] → stays in todo until c0 completes.
    assert c1.status == "todo"
    assert c1.assignee == "engineer"


def test_decompose_records_audit_comment_and_event(kanban_home):
    with kb.connect() as conn:
        tid = _create_triage(conn)
        child_ids = kb.decompose_triage_task(
            conn,
            tid,
            root_assignee="orch",
            children=[{"title": "task A", "assignee": "researcher"}],
            author="alice",
        )
    assert child_ids is not None

    with kb.connect() as conn:
        comments = kb.list_comments(conn, tid)
        events = kb.list_events(conn, tid)

    assert any("Decomposed into" in (c.body or "") for c in comments)
    assert any(ev.kind == "decomposed" for ev in events)


def _create_triage_dir(conn, workspace_path):
    return kb.create_task(
        conn,
        title="code-gen idea",
        assignee=None,
        triage=True,
        workspace_kind="dir",
        workspace_path=workspace_path,
    )


def test_decompose_children_escape_root_dir_without_path(kanban_home):
    """Regression for #100684: a root with kind='dir' but no path used to
    propagate that unusable pair to every child, and resolve_workspace()
    then hard-blocked each spawn (spawn_failed -> gave_up). Children must
    fall back to per-task scratch instead."""
    with kb.connect() as conn:
        tid = _create_triage_dir(conn, workspace_path=None)
        child_ids = kb.decompose_triage_task(
            conn,
            tid,
            root_assignee="orchestrator",
            children=[
                {"title": "child A", "assignee": "researcher"},
                {"title": "child B", "assignee": "engineer", "parents": [0]},
            ],
            author="decomposer",
        )
    assert child_ids is not None

    with kb.connect() as conn:
        for cid in child_ids:
            child = kb.get_task(conn, cid)
            assert child.workspace_kind == "scratch"
            assert not child.workspace_path
            # The dispatch-time contract: resolution must succeed rather
            # than raise "has workspace_kind=dir but no workspace_path".
            resolved = kb.resolve_workspace(child)
            assert resolved == kb.workspaces_root() / child.id
            assert resolved.is_dir()


def test_decompose_children_inherit_root_dir_with_path(kanban_home):
    """The fallback must not weaken normal inheritance: a root dir with a
    valid absolute path still passes kind and path through to children."""
    explicit = kanban_home / "projects" / "feature-x"
    with kb.connect() as conn:
        tid = _create_triage_dir(conn, workspace_path=str(explicit))
        child_ids = kb.decompose_triage_task(
            conn,
            tid,
            root_assignee="orchestrator",
            children=[{"title": "child A", "assignee": "researcher"}],
            author="decomposer",
        )
    assert child_ids is not None

    with kb.connect() as conn:
        child = kb.get_task(conn, child_ids[0])
        assert child.workspace_kind == "dir"
        assert child.workspace_path == str(explicit)
        assert kb.resolve_workspace(child) == explicit




