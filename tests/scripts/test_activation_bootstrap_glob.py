"""scripts/_activation.sh must keep probing under zsh's default nomatch.

``hermes_bootstrap_python`` deliberately lists Windows-only interpreter
candidates (``python.exe``) that never resolve on a POSIX host. zsh aborts
the whole ``for`` word list when any glob in it fails to match, so
``source ./activate`` failed outright on macOS's default shell (#141649)
even though the managed interpreter was installed and Bash activated fine.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.platforms("posix")

# Static on purpose: the probe script (written into the tmp cwd as
# probe.zsh) reads every path from the environment, so no fixture path can
# ever become shell syntax on a command line.
PROBE_BODY = (
    '. "$HERMES_ACTIVATION_SH"; hermes_bootstrap_python "$HERMES_BOOTSTRAP_REPO"\n'
)


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A store holding only the POSIX interpreter beside a venv-less repo.

    Every Windows-only glob candidate in the probe must match nothing, and
    ``repo/../tools`` must not exist either, as on a fresh managed install.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    python = tmp_path / "runtime" / "python-3.14.7" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "probe.zsh").write_text(PROBE_BODY, encoding="utf-8")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "HERMES_HOME": str(home / ".hermes"),
        "HERMES_RUNTIME_DIR": str(tmp_path / "runtime"),
        "HERMES_ACTIVATION_SH": str(ROOT / "scripts" / "_activation.sh"),
        "HERMES_BOOTSTRAP_REPO": str(repo),
    }
    return python, env


@pytest.mark.skipif(
    shutil.which("zsh") is None,
    reason="zsh's nomatch behavior is the regression under test",
)
def test_bootstrap_python_survives_zsh_nomatch(tmp_path):
    python, env = _fixture(tmp_path)
    result = subprocess.run(
        ["zsh", "probe.zsh"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{python}\n"


def test_bootstrap_python_unchanged_under_bash(tmp_path):
    assert shutil.which("bash"), "the shell activation contract requires Bash"
    python, env = _fixture(tmp_path)
    result = subprocess.run(
        ["bash", "probe.zsh"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{python}\n"
