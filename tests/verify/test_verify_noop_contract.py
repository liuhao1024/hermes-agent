"""A verify run that executed nothing must not pass or clear pending verification."""

import json
import socket
from argparse import Namespace

from agent.verify.recipes import Recipe
from agent.verify.runner import run_verify


def _args(root, **kw):
    base = dict(path=str(root), detect_only=False, save=False, skip_start=False, phase=None,
                port=None, timeout=30, ready_timeout=5, json=True)
    base.update(kw)
    return Namespace(**base)


def _manifest(root, recipe):
    (root / ".hermes").mkdir()
    (root / ".hermes" / "environment.json").write_text(json.dumps({"recipe": recipe}))


def test_empty_recipe_is_not_ok(tmp_path):
    result = run_verify(tmp_path, Recipe(name="x"), skip_start=True)
    assert result.phases == [] and result.readiness is None
    assert not result.ok


def test_selected_phase_without_commands_is_not_ok(tmp_path):
    result = run_verify(tmp_path, Recipe(name="x", test=["true"]), phases=("build",))
    assert result.phases == []
    assert not result.ok


def test_real_command_still_ok(tmp_path):
    assert run_verify(tmp_path, Recipe(name="x", test=["true"]), skip_start=True).ok


def test_start_only_readiness_still_ok(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    recipe = Recipe(name="x", start=f"python3 -m http.server {port} --bind 127.0.0.1", port=port)
    result = run_verify(tmp_path, recipe, phases=("start",), ready_timeout=15)
    assert result.phases == [] and result.readiness is not None
    assert result.ok


def test_cli_empty_run_exits_nonzero_and_records_nothing(tmp_path, monkeypatch, capsys):
    from hermes_cli import verify_cmd

    calls = []
    monkeypatch.setattr("agent.verification_evidence.record_verify_run",
                        lambda **kw: calls.append(kw))
    _manifest(tmp_path, {"name": "x", "test": []})
    assert verify_cmd.run_verify_command(_args(tmp_path)) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert calls == []


def test_cli_real_pass_still_records(tmp_path, monkeypatch, capsys):
    from hermes_cli import verify_cmd

    calls = []
    monkeypatch.setattr("agent.verification_evidence.record_verify_run",
                        lambda **kw: calls.append(kw))
    _manifest(tmp_path, {"name": "x", "test": ["true"]})
    assert verify_cmd.run_verify_command(_args(tmp_path, skip_start=True)) == 0
    assert len(calls) == 1 and calls[0]["ok"] is True and calls[0]["scope"] == "targeted"
