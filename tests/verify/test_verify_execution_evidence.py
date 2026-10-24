"""CLI no-op runs must preserve pending edits; real runs keep scope semantics."""
import json
import shlex
import sys
from argparse import ArgumentParser

import pytest

from agent import verification_evidence as evidence
from agent.verification_stop import build_verify_on_stop_nudge
from hermes_cli.subcommands.verify import build_verify_parser
from hermes_cli.verify_cmd import run_verify_command


def _project(tmp_path, monkeypatch, commands):
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "1")
    monkeypatch.setenv("HERMES_SESSION_ID", "verifier")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="verify-fixture"\nversion="0.0.0"\n')
    code = tmp_path / "example.py"
    code.write_text("VALUE = 1\n")
    (tmp_path / ".hermes").mkdir()
    (tmp_path / ".hermes/environment.json").write_text(
        json.dumps({"recipe": {"name": "fixture", "test": commands}})
    )
    for session in ("author", "verifier"):
        assert evidence.mark_workspace_edited(session_id=session, cwd=tmp_path, paths=[str(code)])
    return code


def _invoke(root, flags):
    parser = ArgumentParser()
    build_verify_parser(parser.add_subparsers(), cmd_verify=run_verify_command)
    args = parser.parse_args(["verify", str(root), *flags])
    return args.func(args)


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("selection", [[], ["--phase", "build"], ["--phase", "start", "--skip-start"]])
def test_cli_noop_preserves_pending(tmp_path, monkeypatch, capsys, selection, json_output):
    code = _project(tmp_path, monkeypatch, [])
    before = {sid: evidence.verification_status(session_id=sid, cwd=tmp_path)
              for sid in ("author", "verifier")}
    flags = selection + (["--json"] if json_output else [])
    assert _invoke(tmp_path, flags) == 1
    output = capsys.readouterr().out
    if json_output:
        payload = json.loads(output)
        assert payload["ok"] is False
        assert payload["phases"] == []
        assert payload["readiness"] is None
    else:
        assert "Result: FAILED" in output
        assert "no phases executed" in output
    for sid, status in before.items():
        assert evidence.verification_status(session_id=sid, cwd=tmp_path) == status
        assert build_verify_on_stop_nudge(session_id=sid, changed_paths=[str(code)])
    with evidence._transaction() as conn:
        assert conn.execute("SELECT count(*) FROM verification_events").fetchone()[0] == 0


@pytest.mark.parametrize("exit_code,partial", [(0, False), (0, True), (2, False)])
def test_executed_cli_retains_ledger_semantics(tmp_path, monkeypatch, capsys, exit_code, partial):
    command = f'{shlex.quote(sys.executable)} -B -c "raise SystemExit({exit_code})"'
    _project(tmp_path, monkeypatch, [command])
    flags = ["--json"] + (["--phase", "test"] if partial else [])
    assert _invoke(tmp_path, flags) == (0 if exit_code == 0 else 1)
    payload = json.loads(capsys.readouterr().out)
    assert payload["phases"][0]["exitCode"] == exit_code
    own = evidence.verification_status(session_id="verifier", cwd=tmp_path)
    assert own["status"] == ("passed" if exit_code == 0 else "failed")
    assert own["evidence"]["scope"] == ("targeted" if partial else "full")
    other = evidence.verification_status(session_id="author", cwd=tmp_path)
    if exit_code == 0 and not partial:
        assert other["status"] == "passed"
    else:
        assert other["status"] != "passed"


def test_detect_only_does_not_record(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, [])
    before = evidence.verification_status(session_id="verifier", cwd=tmp_path)
    assert _invoke(tmp_path, ["--detect-only", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["recipe"]["name"] == "fixture"
    assert evidence.verification_status(session_id="verifier", cwd=tmp_path) == before
