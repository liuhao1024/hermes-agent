"""Dispatch refuses a worktree workspace for an SSH-backend assignee (#143249).

The dispatcher materializes ``worktree`` tasks with the HOST git, so the
linked checkout's ``.git`` pointer names a host-side gitdir. A worker whose
profile runs ``terminal.backend: ssh`` executes every tool in a remote shell
that cannot resolve that pointer (``fatal: not a git repository``): the board
accepted the card as healthy and the worker failed on every git touch, with
nothing on the board naming the mismatch. The dispatcher now refuses the
combination at claim time — one explicit ``spawn_failed`` (force-tripped: no
retry can heal a profile's backend choice) instead of a spawned worker that
can never use its workspace.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd


def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _assignee_profile(tmp_path, monkeypatch, *, backend: str | None) -> Path:
    """A 'worker' profile whose config.yaml pins ``terminal.backend``.

    ``resolve_profile_env`` is pinned to the profile dir so the dispatch-side
    read sees this config.yaml regardless of the ambient profile layout.
    """
    worker = tmp_path / "profiles-home" / "worker"
    worker.mkdir(parents=True)
    if backend is not None:
        (worker / "config.yaml").write_text(
            f"terminal:\n  backend: {backend}\n", encoding="utf-8")
    from hermes_cli import profiles as profiles_mod
    monkeypatch.setattr(profiles_mod, "profile_exists", lambda name: True)
    monkeypatch.setattr(profiles_mod, "resolve_profile_env", lambda name: str(worker))
    return worker


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)],
                   check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@e.co",
         "-c", "commit.gpgsign=false", "add", "README.md"],
        check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@e.co",
         "-c", "commit.gpgsign=false", "commit", "-m", "init"],
        check=True, capture_output=True, text=True)
    return repo


def _worktree_task(conn, repo: Path) -> str:
    tid = kb.create_task(conn, title="probe", assignee="worker")
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE tasks SET workspace_kind='worktree', workspace_path=? WHERE id = ?",
            (str(repo), tid),
        )
    return tid


def test_ssh_backend_worktree_refused_at_dispatch(tmp_path, monkeypatch):
    """RED (#143249): an explicit ssh backend force-blocks the card at claim
    time with the mismatch named, instead of spawning a worker that fails on
    every git touch."""
    _isolated_home(tmp_path, monkeypatch)
    _assignee_profile(tmp_path, monkeypatch, backend="ssh")
    repo = _make_repo(tmp_path)
    spawned = []
    with kbc.connect() as conn:
        tid = _worktree_task(conn, repo)
        res = kbd.dispatch_once(conn, dry_run=False, spawn_fn=lambda t, w: spawned.append(t.id))
        task = kb.get_task(conn, tid)
        events = [e for e in kb.list_events(conn, tid) if e.kind == "gave_up"]
    assert not spawned, "no worker may be spawned for an ssh-backed worktree card"
    assert res.auto_blocked == [tid]
    assert task is not None and task.status == "blocked"
    assert task.last_failure_error and "terminal.backend=ssh" in task.last_failure_error
    assert events, "the refusal must be visible as a board event for show/tail"
    assert "terminal.backend=ssh" in str(events[0].payload.get("error", ""))
    assert events[0].payload.get("trigger_outcome") == "spawn_failed"
    assert events[0].payload.get("sticky") is True


def test_ssh_refusal_is_sticky_not_a_retry_loop(tmp_path, monkeypatch):
    """The condition cannot heal on retry: repeated ticks keep the card
    blocked (one failure event per tick is fine, but the card must never
    return to ready)."""
    _isolated_home(tmp_path, monkeypatch)
    _assignee_profile(tmp_path, monkeypatch, backend="ssh")
    repo = _make_repo(tmp_path)
    with kbc.connect() as conn:
        tid = _worktree_task(conn, repo)
        for _ in range(3):
            kbd.dispatch_once(conn, dry_run=False, spawn_fn=lambda t, w: None)
        task = kb.get_task(conn, tid)
    assert task is not None and task.status == "blocked"


def test_local_backend_worktree_still_dispatches(tmp_path, monkeypatch):
    """The refusal is ssh-specific: a local backend (or none pinned) keeps
    today's spawn path — the worktree resolves and the worker is spawned."""
    _isolated_home(tmp_path, monkeypatch)
    _assignee_profile(tmp_path, monkeypatch, backend="local")
    repo = _make_repo(tmp_path)
    spawned = []
    with kbc.connect() as conn:
        tid = _worktree_task(conn, repo)
        res = kbd.dispatch_once(conn, dry_run=False, spawn_fn=lambda t, w: spawned.append(t.id))
        task = kb.get_task(conn, tid)
    assert spawned == [tid]
    assert res.auto_blocked == []
    assert task is not None and task.status == "running"


def test_container_backend_worktree_not_refused(tmp_path, monkeypatch):
    """Container backends translate host paths in the tool layer; only ssh is
    refused (#143249 names ssh specifically)."""
    _isolated_home(tmp_path, monkeypatch)
    _assignee_profile(tmp_path, monkeypatch, backend="docker")
    repo = _make_repo(tmp_path)
    spawned = []
    with kbc.connect() as conn:
        tid = _worktree_task(conn, repo)
        res = kbd.dispatch_once(conn, dry_run=False, spawn_fn=lambda t, w: spawned.append(t.id))
    assert spawned == [tid]
    assert res.auto_blocked == []


def test_unpinned_backend_worktree_not_refused(tmp_path, monkeypatch):
    """No explicit terminal.backend in the profile config (the default local)
    must keep the current behaviour — fail-open on the config read."""
    _isolated_home(tmp_path, monkeypatch)
    _assignee_profile(tmp_path, monkeypatch, backend=None)
    repo = _make_repo(tmp_path)
    spawned = []
    with kbc.connect() as conn:
        tid = _worktree_task(conn, repo)
        res = kbd.dispatch_once(conn, dry_run=False, spawn_fn=lambda t, w: spawned.append(t.id))
    assert spawned == [tid]
    assert res.auto_blocked == []
