"""An environment selection record this install cannot understand must not
become a permanent refusal. A side environment left by a pre-``gen-`` layout
carries a bare digest and a legacy key set; refusing it at every read wedged
readers like the source-update completion tail forever, and no retry could
help. A readable record in an unrecognized shape is therefore no selection:
readers degrade, the next ensure rebuilds, and the re-selection is written in
the current format. Only a record that cannot be read at all is corruption
worth refusing.
"""
from __future__ import annotations

import json

import pytest

from pm.package import InstallError

LEGACY_RECORD = {
    "environment": "/installs/36da5305/.generations/project-test-environment"
                   "/e4b106ad1d9404067477d20766950ffa6593ad9e64666d433910b62d8ce0f4c2/venv",
    "extras": ["all"],
    "generation": "e4b106ad1d9404067477d20766950ffa6593ad9e64666d433910b62d8ce0f4c2",
    "groups": ["dev", "test"],
    "inputs": "e4b106ad1d9404067477d20766950ffa6593ad9e64666d433910b62d8ce0f4c2",
}


def _write_selection(root, record) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "active.json").write_text(json.dumps(record), encoding="utf-8")


def _fixture_python(environment):
    from pm import operations

    python = operations._python(environment)
    python.parent.mkdir(parents=True)
    python.touch()
    (environment / "pyvenv.cfg").write_text("home = fixture\n", encoding="utf-8")
    return python


def test_legacy_selection_reads_as_no_selection(tmp_path):
    from pm import operations

    root = tmp_path / "side"
    _write_selection(root, LEGACY_RECORD)
    assert operations.environment_python("proof", root=root) is None


def test_unrecognized_selection_names_the_drift(tmp_path, capsys):
    from pm import operations

    root = tmp_path / "side"
    _write_selection(root, LEGACY_RECORD)
    operations.environment_python("proof", root=root)
    assert "unrecognized format" in capsys.readouterr().err


@pytest.mark.parametrize("record", [
    ["not", "a", "dict"],
    {"generation": None},
    {"generation": "production"},
])
def test_unrecognized_shapes_read_as_no_selection(tmp_path, record):
    from pm import operations

    root = tmp_path / "side"
    _write_selection(root, record)
    assert operations.environment_python("proof", root=root) is None


def test_unreadable_selection_is_still_refused(tmp_path):
    from pm import operations

    root = tmp_path / "side"
    root.mkdir()
    (root / "active.json").write_text("{ truncated", encoding="utf-8")
    with pytest.raises(InstallError, match="cannot read"):
        operations.environment_python("proof", root=root)


def test_current_format_selection_still_resolves(tmp_path):
    from pm import operations

    root = tmp_path / "side"
    python = _fixture_python(root / ("gen-" + "a" * 32) / "venv")
    _write_selection(root, {"generation": "gen-" + "a" * 32, "inputs": "x"})
    assert operations.environment_python("proof", root=root) == python


def test_ensure_rebuilds_over_an_unrecognized_selection(tmp_path, monkeypatch):
    from pm import _uv, operations

    root = tmp_path / "side"
    _write_selection(root, LEGACY_RECORD)
    base = tmp_path / "base" / "python"
    base.parent.mkdir()
    base.touch()
    monkeypatch.setattr(_uv, "_toolchain", lambda **kwargs: (None, base))

    def build(generation, base_python):
        return _fixture_python(generation / "venv")

    python = operations._ensure_generation("proof", root, {}, build,
                                           record={}, explicit=True)
    assert operations.environment_python("proof", root=root) == python
    selected = json.loads((root / "active.json").read_text(encoding="utf-8"))
    assert selected["generation"].startswith("gen-")
