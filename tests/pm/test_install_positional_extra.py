"""`pm install` positionals are store packages; extras take `--extra`.

Two dead-ends on a source install whose store drifts from the lock (dev
checkout pinned newer tools than its store holds):

1. ``hermes pm install <extra-name>`` raised a raw ``KeyError`` traceback —
   the registry only knows store packages, and the extra namespace is
   disjoint from it. The user needs the same remedy the missing-extra hint
   prints (``install_hint``), not a stack trace.
2. ``hermes pm install --extra <name>`` (the remedy every missing-extra
   hint names) hard-failed on pre-existing drift of a tool the command
   never installs (``node``), because the pre-sync PATH gate ran even when
   the tool closure was empty. The venv build resolves its own pinned uv +
   python from the store by absolute path — node is not in its chain — so
   the gate must not fire for a command that publishes no tools. The gate
   still guards every install that publishes the whole tool closure (full
   closure, ``--tools-only``): 9c11bb96bd's invariant. Narrow installs
   (named tools, with or without ``--extra``) stay exempt, as 9c11bb96bd
   left them.
"""
from types import SimpleNamespace

import pytest

from pm import cli, install_hint
from pm import install as install_mod
from pm.registry import get_package


@pytest.fixture
def no_installs(monkeypatch):
    """Record, never execute: no store install or venv sync in these tests.

    activate() records its call and reports a clean tool path by default;
    the gate tests override it to refuse.
    """
    calls = {"ensure": [], "activate": []}

    monkeypatch.setattr(cli, "ensure",
                        lambda name, *args, **kwargs: (calls["ensure"].append(name),
                                                       SimpleNamespace())[1])
    monkeypatch.setattr(install_mod, "sync_venv", lambda extras=None, **kwargs: None)

    def fake_activate(**kwargs):
        calls["activate"].append(kwargs)
        return []

    monkeypatch.setattr(install_mod, "activate", fake_activate)
    monkeypatch.setattr(cli, "_install_defaults", lambda names, verify: None)
    def _noop_progress():
        def noop(stage, done, total, label=""):
            pass

        noop.finish = lambda: None
        return noop

    monkeypatch.setattr(cli, "_live_progress", lambda name: _noop_progress())
    return calls


def _refuse_gate(calls):
    """The drifted-store condition: the store's tools cannot be put on PATH."""
    def refuse(**kwargs):
        calls["activate"].append(kwargs)
        return ["node: not installed or outdated"]

    return refuse


def _install_args(names=(), extra=()):
    return SimpleNamespace(names=list(names), extra=list(extra), target=None,
                           tools_only=False, trust_recorded=False,
                           without=[], test_environment=None)


def test_positional_naming_a_declared_extra_is_a_clean_remedy(no_installs, capsys):
    """`pm install stt-whisper`: an actionable message naming the `--extra`
    remedy, exit 1, and the name never reaches the installer."""
    assert cli.cmd_install(_install_args(names=["stt-whisper"])) == 1
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert "KeyError" not in out
    assert install_hint("stt-whisper") in out
    assert "stt-whisper" not in no_installs["ensure"]


def test_positional_naming_nothing_known_is_a_clean_error(no_installs, capsys):
    """A name that is neither a store package nor a declared extra still
    gets a message (with the doctor pointer), not a raw KeyError."""
    with pytest.raises(KeyError):
        get_package("no-such-store-tool")  # premise: the registry rejects it

    assert cli.cmd_install(_install_args(names=["no-such-store-tool"])) == 1
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert "no-such-store-tool" in out
    assert "hermes pm doctor" in out
    assert "no-such-store-tool" not in no_installs["ensure"]


def test_pure_extra_sync_skips_the_pre_sync_tool_path_gate(no_installs, capsys, monkeypatch):
    """A drifted node must not fail a `--extra` sync: the command installs no
    tools, and the venv build does not use node at all. Even a refusing
    gate never runs for an empty tool closure."""
    monkeypatch.setattr(install_mod, "activate", _refuse_gate(no_installs))

    assert cli.cmd_install(_install_args(extra=["stt-whisper"])) == 0
    out = capsys.readouterr().out
    assert "tools not on PATH" not in out
    assert no_installs["activate"] == []  # the gate did not run at all


def test_full_closure_install_still_gates_on_the_tool_path(no_installs, capsys, monkeypatch):
    """The 9c11bb96bd invariant is preserved: a full-closure install that
    publishes tools still refuses a sync when its tools cannot be put on
    PATH (the Windows ARM64 compiler case)."""
    monkeypatch.setattr(install_mod, "activate", _refuse_gate(no_installs))

    assert cli.cmd_install(_install_args()) == 1
    out = capsys.readouterr().out
    assert "tools not on PATH before venv sync: node: not installed or outdated" in out
    assert no_installs["activate"] == [{"allow_incomplete": True}]


def test_named_tool_install_skips_the_pre_sync_tool_path_gate(no_installs, capsys, monkeypatch):
    """Naming tools is a narrow install: it installs only those and does not
    sync the full closure, so the whole-closure gate never applied to it
    (9c11bb96bd left it out of the gate condition on purpose). Drifted
    tools must not fail it either."""
    monkeypatch.setattr(install_mod, "activate", _refuse_gate(no_installs))

    assert cli.cmd_install(_install_args(names=["node"])) == 0
    out = capsys.readouterr().out
    assert "tools not on PATH" not in out
    assert no_installs["activate"] == []
    assert "node" in no_installs["ensure"]  # the named tool still installed


def test_named_tools_plus_extra_still_skip_the_gate(no_installs, capsys, monkeypatch):
    """`pm install node --extra X` is still a narrow install (named tools +
    the named extras); the gate stays out of it and the sync carries the
    extras."""
    monkeypatch.setattr(install_mod, "activate", _refuse_gate(no_installs))

    assert cli.cmd_install(_install_args(names=["node"], extra=["stt-whisper"])) == 0
    out = capsys.readouterr().out
    assert "tools not on PATH" not in out
    assert no_installs["activate"] == []
