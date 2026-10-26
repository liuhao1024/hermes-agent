"""The auto-decompose tick must skip parked (unassigned) triage cards.

Regression for #144887: the desktop New-task form promises ``unassigned
(parked — won't run)`` by leaving the assignee empty, but ``assignee IS
NULL`` was the entire representation — the tick enumerated every
``status="triage"`` card regardless and decomposed parked cards into
executable assigned children. The automatic path now excludes unassigned
cards; the explicit ``kanban decompose --all`` / ``specify --all`` paths
keep enumerating them (an explicit command is the release).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from gateway import kanban_watchers_dispatcher as kwd
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
import hermes_cli
import hermes_cli.kanban_decompose as dec


def _fresh_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for var in ("HERMES_KANBAN_DB", "HERMES_KANBAN_WORKSPACES_ROOT", "HERMES_KANBAN_HOME", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(var, raising=False)
    try:
        import hermes_constants
        hermes_constants._cached_default_hermes_root = None  # type: ignore[attr-defined]
    except Exception:
        pass
    kb._INITIALIZED_PATHS.clear()


def test_auto_tick_excludes_unassigned_triage(monkeypatch):
    seen: dict = {}

    def fake_list(*args, **kwargs):
        seen.update(kwargs)
        return ["t1"]

    def fake_decompose(task_id, author=None):
        return SimpleNamespace(ok=True, fanout=False, child_ids=None, reason=None)

    fake = SimpleNamespace(list_triage_ids=fake_list, decompose_task=fake_decompose)
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_decompose", fake)
    monkeypatch.setattr(hermes_cli, "kanban_decompose", fake, raising=False)
    monkeypatch.setattr(kwd, "_board_slugs", lambda kb: ["default"])

    settings = kwd._DispatcherSettings(60.0, None, None, 2, 0, True, None, None)
    dispatcher = kwd._KanbanDispatcher(SimpleNamespace(DEFAULT_BOARD="default"), settings)

    assert dispatcher.auto_decompose_tick(5) == 1
    assert seen.get("exclude_unassigned") is True


def test_list_triage_ids_exclude_unassigned(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)
    with kbc.connect_closing() as conn:
        assigned = kb.create_task(conn, title="routed work", assignee="alice", triage=True)
        parked = kb.create_task(conn, title="parked idea", triage=True)

    assert set(dec.list_triage_ids()) == {assigned, parked}
    assert dec.list_triage_ids(exclude_unassigned=True) == [assigned]
