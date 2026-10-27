"""Side-environment refusals must name their own explicit preparation operation."""
from __future__ import annotations

import json

import pytest

from pm import _uv, operations
from pm.package import InstallError


@pytest.fixture
def side_environment(tmp_path, monkeypatch):
    base = tmp_path / "base-python"
    base.touch()
    monkeypatch.setattr(_uv, "_toolchain", lambda **kwargs: (None, base))
    monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "1")
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="1"\n')
    (project / "uv.lock").write_text("version = 1\n")
    builds = []

    def build(**kwargs):
        builds.append(kwargs)
        python = operations._python(kwargs["out"])
        python.parent.mkdir(parents=True)
        python.touch()
        (kwargs["out"] / "pyvenv.cfg").write_text("home = fixture\n")
        return python

    monkeypatch.setattr(operations, "build_environment", build)
    # Synthetic builder: avoid a machine-wide disk flush for fixture-only files.
    monkeypatch.setattr("pm.environments.flush_before_selecting", lambda: None)
    return project, tmp_path / "side", builds


@pytest.mark.parametrize("kind", ["project", "requirements"])
@pytest.mark.parametrize("state", ["missing", "stale"])
def test_side_refusal_names_its_preparation_api(side_environment, kind, state):
    project, root, builds = side_environment

    def ensure(explicit=False):
        if kind == "project":
            return operations.ensure_project_environment(
                "test-environment", project, extras=["all"], groups=["dev", "test"],
                root=root, explicit=explicit)
        return operations.ensure_environment("formatter", ["example==1"], root=root, explicit=explicit)

    if state == "stale":
        ensure(explicit=True)
        record = json.loads((root / "active.json").read_text())
        record["inputs"] = "obsolete"
        (root / "active.json").write_text(json.dumps(record))
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    build_count = len(builds)
    with pytest.raises(InstallError) as refused:
        ensure()
    error = refused.value
    operation = "ensure_project_environment" if kind == "project" else "ensure_environment"
    assert f"pm.{operation}" in error.remedy
    assert "explicit=True" in error.remedy
    assert "same" in error.remedy and "root" in error.remedy
    assert "hermes pm install" not in error.remedy
    assert "allow_lazy_installs" not in error.remedy
    assert len(builds) == build_count
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    if state == "missing":
        assert not root.exists()
    # The remedy is also what the app-side consumer reads after a worker failure.
    from pm.client import _raise_worker_error
    with pytest.raises(InstallError) as received:
        _raise_worker_error({"type": "InstallError", "package": error.package, "cause": error.cause, "remedy": error.remedy})
    assert str(received.value) == str(error)
    assert ensure(explicit=True).is_file()
    assert len(builds) == build_count + 1
    assert ensure() == operations.environment_python("test-environment", root=root)
    assert len(builds) == build_count + 1
    if kind == "project":
        assert builds[-1]["no_install_project"] is True
        assert builds[-1]["frozen"] is True


def test_package_refusal_keeps_package_install_remedy():
    from pm.install import _refuse_lazy
    assert "hermes pm install" in _refuse_lazy("python", "missing tool").remedy


@pytest.mark.parametrize("kind", ["project", "requirements", "tool"])
@pytest.mark.parametrize("state", ["missing", "stale"])
def test_named_public_remedy_prepares_and_reuses(side_environment, monkeypatch, kind, state):
    import re

    import pm
    from pm import client, receipt

    project, root, builds = side_environment
    requirements = ["example==1"]
    tools, refusals = [], []
    original_toolchain = _uv._toolchain
    original_build = operations.build_environment

    def toolchain(**kwargs):
        tools.append(kwargs)
        assert kwargs == {"realize": False}
        return original_toolchain(**kwargs)

    def build(**kwargs):
        python = original_build(**kwargs)
        (python.parent / "fixture-tool").touch()
        return python

    monkeypatch.setattr(_uv, "_toolchain", toolchain)
    monkeypatch.setattr(operations, "build_environment", build)
    monkeypatch.setattr(receipt, "record_refusal", lambda *args: refusals.append(args))
    # Public facade/client and real dispatch; only the IPC/runtime boundary is a seam.
    monkeypatch.setattr(client, "is_runtime", lambda: True)
    arguments = {"name": "fixture-side", "root": root}
    if kind == "project":
        api = pm.ensure_project_environment
        arguments.update(project=project, extras=["all"], groups=["test", "dev"])
    else:
        api = pm.ensure_python_tool if kind == "tool" else pm.ensure_environment
        arguments.update(requirements=requirements)
        if kind == "tool":
            arguments["executable"] = "fixture-tool"

    def tree():
        return {str(p.relative_to(root)): None if p.is_dir() else p.read_bytes()
                for p in root.rglob("*")}

    if state == "stale":
        previous = api(**arguments, explicit=True)
        if kind == "project":
            (project / "uv.lock").write_text("version = 2\n")
        else:
            requirements[:] = ["example==2"]
    before, count = tree(), len(builds)
    with pytest.raises(InstallError) as refused:
        api(**arguments)
    error = refused.value
    assert tree() == before and len(builds) == count
    if state == "missing":
        assert not root.exists()
    assert refusals == [("lazy-install", str(error))]
    assert "explicit=True" in error.remedy
    assert "hermes pm install" not in error.remedy
    assert "allow_lazy_installs" not in error.remedy
    with pytest.raises(InstallError) as decoded:
        client._raise_worker_error({"type": "InstallError", "package": error.package,
                                   "cause": error.cause, "remedy": error.remedy})
    assert decoded.value.remedy == error.remedy

    # Interpret only this known diagnostic grammar, never evaluate arbitrary advice.
    match = re.fullmatch(r"explicitly call pm\.(\w+) with explicit=True and the same "
                         r"([a-z, ]+); this does not sync the application environment", error.remedy)
    assert match is not None, error.remedy
    supported = {"ensure_environment": pm.ensure_environment,
                 "ensure_python_tool": pm.ensure_python_tool,
                 "ensure_project_environment": pm.ensure_project_environment}
    assert match[1] in supported
    fields = re.split(r", | and ", match[2])
    known = {**arguments, "executable": arguments.get("executable")}
    assert set(fields) <= known.keys() and len(fields) == len(set(fields))
    # Actually follow every advertised field. Old candidate raises TypeError here,
    # even for requirements (executable=None is not a public parameter).
    current = supported[match[1]](**{key: known[key] for key in fields}, explicit=True)
    assert supported[match[1]] is api and set(fields) == arguments.keys()
    assert current.is_file() and len(builds) == count + 1
    if kind == "tool":
        assert current.name == "fixture-tool"
    if state == "stale":
        assert previous.is_file() and previous != current
    stable = tree()
    assert api(**arguments) == current
    assert tree() == stable and len(builds) == count + 1
    if kind == "project":
        assert builds[-1]["no_install_project"] is True and builds[-1]["frozen"] is True
        assert builds[-1]["groups"] == ["dev", "test"]
    assert tools and all(item == {"realize": False} for item in tools)
