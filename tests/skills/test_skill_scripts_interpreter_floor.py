"""Skill helper scripts must keep running under any host `python3`.

SKILL.md recipes invoke these stdlib-only helpers with whatever interpreter
the host resolves for `python3` — on macOS the login shell's path_helper puts
Apple's 3.9 first, and fresh Linux boxes vary. Referencing ``datetime.UTC``
(a Python 3.11+ addition, reintroduced repo-wide by an UP017 pass) breaks the
skill at import time even though the repo's requires-python floor is 3.11
(issue #138239). Guard that floor mechanically so a future lint pass cannot
regress it; ``timezone.utc`` is the spelling that works everywhere.
"""
import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT_GLOBS = ("skills/**/scripts/*.py", "optional-skills/**/scripts/*.py")


def _skill_scripts() -> list[Path]:
    scripts: list[Path] = []
    for pattern in SCRIPT_GLOBS:
        scripts.extend(REPO.glob(pattern))
    return sorted(scripts)


@pytest.mark.parametrize(
    "script",
    _skill_scripts(),
    ids=lambda p: str(p.relative_to(REPO)),
)
def test_skill_script_keeps_off_py311_datetime_utc(script: Path) -> None:
    tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "datetime"
            and any(alias.name == "UTC" for alias in node.names)
        ):
            pytest.fail(
                f"{script.relative_to(REPO)} imports datetime.UTC (Python 3.11+); "
                "skill scripts run under the host python3 (macOS resolves 3.9) — "
                "use timezone.utc instead"
            )
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "UTC"
            and isinstance(node.value, ast.Name)
        ):
            pytest.fail(
                f"{script.relative_to(REPO)} references {node.value.id}.UTC "
                "(Python 3.11+ datetime attribute); skill scripts run under the "
                "host python3 (macOS resolves 3.9) — use "
                f"{node.value.id}.timezone.utc instead"
            )
