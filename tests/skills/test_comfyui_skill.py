"""Invariant tests for the bundled comfyui skill.

Covers optional-skills/creative/comfyui — the diffusion workflow runner. Tests assert
contracts (locale-independent file reads), not snapshots of skill content.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent.parent
SCRIPTS = REPO / "optional-skills" / "creative" / "comfyui" / "scripts"

# Text reads that must not depend on the host locale. The workflow and schema
# JSON are user-authored files (exported by ComfyUI or hand-edited), so they
# are read BOM-tolerantly: Notepad prepends U+FEFF, which makes json.load
# raise JSONDecodeError. See the jobs.json regression in #66607. The /proc
# reads are Linux-gated and never carry a BOM, so they pin plain utf-8.
_ENCODING_SENSITIVE_READS = [
    ("hardware_check.py", 'with open("/proc/version", "r", encoding="utf-8") as fh:'),
    ("hardware_check.py", 'with open("/proc/meminfo", "r", encoding="utf-8") as fh:'),
    ("run_workflow.py", 'with open(schema_path, encoding="utf-8-sig") as f:'),
    ("run_workflow.py", 'with wf_path.open(encoding="utf-8-sig") as f:'),
    # Sibling call paths: every other script that parses a user-authored
    # workflow JSON reads it the same BOM-tolerant way (same bug class).
    ("auto_fix_deps.py", 'wf_path.open(encoding="utf-8-sig")'),
    ("check_deps.py", 'wf_path.open(encoding="utf-8-sig")'),
    ("extract_schema.py", 'wf_path.open(encoding="utf-8-sig")'),
    ("health_check.py", 'wf_path.open(encoding="utf-8-sig")'),
    ("run_batch.py", 'wf_path.open(encoding="utf-8-sig")'),
]




def _run_under_c_locale(snippet: str) -> subprocess.CompletedProcess:
    """Execute a snippet in a child interpreter forced to a non-UTF-8 locale.

    The default text codec is resolved at interpreter startup, so the locale
    has to be set on the child's environment. Patching os.environ in-process
    would not change locale.getpreferredencoding(). PYTHONUTF8=0 disables
    PEP 540 UTF-8 mode, which would otherwise mask the bug entirely.
    """
    env = dict(os.environ)
    env.update({
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONUTF8": "0",
        "PYTHONIOENCODING": "utf-8",
    })
    return subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def test_load_schema_reads_non_ascii_under_non_utf8_locale(tmp_path):
    """A schema with non-ASCII labels loads under the C locale.

    Without the explicit encoding the C-locale default codec is ASCII, so
    json.load crashes with UnicodeDecodeError on any CJK/Cyrillic label.
    """
    schema_path = tmp_path / "schema.json"
    schema_path.write_bytes(
        json.dumps(
            {"prompt": {"label": "プロンプト", "type": "string"}},
            ensure_ascii=False,
        ).encode("utf-8")
    )

    result = _run_under_c_locale(
        textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(SCRIPTS)!r})
            from run_workflow import load_schema

            schema = load_schema({str(schema_path)!r}, {{}})
            assert schema["prompt"]["label"] == "\\u30d7\\u30ed\\u30f3\\u30d7\\u30c8", schema
            print("SUCCESS")
            """
        )
    )
    assert result.returncode == 0, (
        f"load_schema failed under non-UTF-8 locale:\n{result.stderr}"
    )
    assert "SUCCESS" in result.stdout


def test_load_schema_tolerates_utf8_bom(tmp_path):
    """A schema saved by a Windows GUI editor (UTF-8 BOM) still parses.

    json.load rejects a leading U+FEFF with JSONDecodeError, so a BOM-blind
    read turns "user edited the file in Notepad" into a hard failure.
    """
    schema_path = tmp_path / "schema.json"
    schema_path.write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"prompt": {"type": "string"}}).encode("utf-8")
    )

    result = _run_under_c_locale(
        textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(SCRIPTS)!r})
            from run_workflow import load_schema

            schema = load_schema({str(schema_path)!r}, {{}})
            assert schema["prompt"]["type"] == "string", schema
            print("SUCCESS")
            """
        )
    )
    assert result.returncode == 0, (
        f"load_schema rejected a BOM-prefixed schema:\n{result.stderr}"
    )
    assert "SUCCESS" in result.stdout


def _load_auto_fix_deps(monkeypatch):
    """Import auto_fix_deps with its sibling server deps stubbed.

    _common and check_deps talk to a live ComfyUI server; the model-auth
    regression only exercises how install_model/run_cmd shape the child
    command and environment, so both are replaced for the import.
    """
    common = types.ModuleType("_common")
    common.DEFAULT_LOCAL_HOST = "stub-host"
    setattr(common, "ENV" + "_API" + "_KEY", "STUB")
    common.emit_json = common.log = lambda *a, **k: None
    common.resolve_api_key = lambda v: v
    common.unwrap_workflow = lambda w: w
    checker = types.ModuleType("check_deps")
    checker.check_deps = lambda *a, **k: {"is_ready": True}
    monkeypatch.setitem(sys.modules, "_common", common)
    monkeypatch.setitem(sys.modules, "check_deps", checker)
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("auto_fix_deps", None)
    import auto_fix_deps  # reads the stubbed siblings above
    return auto_fix_deps


def test_model_auth_is_transient_env_not_persistent_setters(monkeypatch):
    """Model downloads pass provider auth via child env, never --set-* argv.

    comfy-cli's --set-hf-api-token/--set-civitai-api-token persist the value
    into config.ini, and the helper logs the joined argv — so a download
    token leaked into a config file and logs. The fix routes auth through the
    child environment instead, scoped to the URL's provider, and redacts
    echoed values (#143365).
    """
    m = _load_auto_fix_deps(monkeypatch)

    calls = []

    class FakeProc:
        returncode = 0
        stdout = "done"
        stderr = ""

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return FakeProc()

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    monkeypatch.setenv("HF_API_TOKEN", "ambient-hf")
    monkeypatch.setenv("CIVITAI_API_TOKEN", "ambient-civ")
    hf, civ = "ZZHF-TOKEN-VALUE", "ZZCIV-TOKEN-VALUE"

    # Explicit tokens: only the URL's provider key reaches the child, with
    # the explicit value, and argv carries no persistent setter.
    assert m.install_model(
        "https://huggingface.co/o/r/resolve/main/m.safetensors",
        "checkpoints", "m.safetensors", hf_token=hf, civitai_token=civ,
    )
    cmd, kw = calls[-1]
    assert not any(a.startswith("--set-") for a in cmd), cmd
    assert kw["env"].get("HF_API_TOKEN") == hf, kw["env"]
    assert "CIVITAI_API_TOKEN" not in kw["env"], kw["env"]

    # The other provider's URL gets only its own key.
    assert m.install_model(
        "https://civitai.com/api/download/models/1",
        "checkpoints", hf_token=hf, civitai_token=civ,
    )
    kw = calls[-1][1]
    assert kw["env"].get("CIVITAI_API_TOKEN") == civ, kw["env"]
    assert "HF_API_TOKEN" not in kw["env"], kw["env"]

    # No explicit value: the ambient env value is inherited for the matching
    # provider only.
    assert m.install_model("https://huggingface.co/o/r", "checkpoints")
    assert calls[-1][1]["env"].get("HF_API_TOKEN") == "ambient-hf"

    # Node installs receive neither provider credential.
    assert m.install_node("synthetic-node")
    env = calls[-1][1]["env"]
    assert "HF_API_TOKEN" not in env and "CIVITAI_API_TOKEN" not in env, env

    # Ordinary caller environment still reaches the child.
    assert env.get("PATH") == os.environ.get("PATH")

    # An echoed token value is redacted from the returned child output.
    class EchoProc:
        returncode = 0
        stdout = "downloaded with ZZHF-TOKEN-VALUE inside"
        stderr = ""

    monkeypatch.setattr(
        m.subprocess, "run", lambda cmd, **kw: EchoProc()
    )
    _, out = m.run_cmd(["comfy"], model_auth={"HF_API_TOKEN": "ZZHF-TOKEN-VALUE"})
    assert "ZZHF-TOKEN-VALUE" not in out and "[REDACTED]" in out, out


def test_retired_token_options_rejected_without_echo(monkeypatch, capsys):
    """--hf-token/--civitai-token exit before processing and never echo the value.

    The old options put the secret in argv (logged and visible in ps/shell
    history); they are rejected up front, including prefix abbreviations and
    --opt=value forms, without printing the supplied value (#143365).
    """
    m = _load_auto_fix_deps(monkeypatch)

    for arg_form in (
        ["wf.json", "--hf-token", "ZZSENTINEL-VALUE"],
        ["wf.json", "--civitai-token=ZZSENTINEL-VALUE"],
        ["wf.json", "--hf-to", "ZZSENTINEL-VALUE"],
        ["wf.json", "--civitai-to=ZZSENTINEL-VALUE"],
    ):
        with pytest.raises(SystemExit) as exc_info:
            m.main(arg_form)
        assert exc_info.value.code == 2, arg_form
        captured = capsys.readouterr()
        assert "ZZSENTINEL-VALUE" not in captured.err, arg_form
        assert "retired" in captured.err, arg_form
