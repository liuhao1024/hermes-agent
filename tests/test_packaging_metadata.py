"""Independent core/optional dependency and reviewed CVE policies."""
import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_exact_pinned_deps_exempt_from_exclude_newer():
    """Every exact-pinned dependency must appear in [tool.uv.exclude-newer-package].

    uv reads a missing upload-time as "newer than the cutoff" and filters the
    pinned version out, so an exact pin on a mirror index without upload-time
    bricks resolution (#131681 pilk shape: "pilk==0.2.4 has no publish time").
    A pin cannot float without a reviewed bump, so exemption (``false`` or a
    per-package timestamp) removes that brick risk at zero supply-chain cost.
    """
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    exempt = {_pep503(name) for name in manifest["tool"]["uv"]["exclude-newer-package"]}
    requirement_groups = [manifest["project"]["dependencies"]]
    requirement_groups += manifest["project"]["optional-dependencies"].values()
    requirement_groups += [manifest.get("build-system", {}).get("requires", [])]
    requirement_groups += manifest.get("dependency-groups", {}).values()
    pinned = set()
    for group in requirement_groups:
        for requirement in map(Requirement, group):
            specs = list(requirement.specifier)
            # `==1.2.*` wildcards can float within the prefix — only bare `==` pins
            # are frozen to one reviewed version.
            if (len(specs) == 1 and specs[0].operator == "=="
                    and not specs[0].version.endswith(".*")):
                pinned.add(_pep503(requirement.name))
    assert pinned <= exempt, (
        "exact-pinned deps missing from [tool.uv.exclude-newer-package]: "
        f"{sorted(pinned - exempt)}"
    )


def test_test_dependencies_are_group_only_in_manifest_and_lock():
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    hermes = next(package for package in lock["package"] if package["name"] == manifest["project"]["name"])
    assert manifest["tool"]["uv"]["default-groups"] == []
    assert "dev" in manifest["dependency-groups"]
    assert "dev" not in manifest["project"]["optional-dependencies"]
    assert "dev" in hermes["dev-dependencies"]
    assert "dev" not in hermes.get("optional-dependencies", {})


def test_core_and_optional_speech_dependencies():
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    core = {Requirement(dep).name for dep in project["dependencies"]}
    assert "packaging" in core  # Runtime code imports it directly, not transitively.
    assert "faster-whisper" not in core
    assert "faster-whisper" in {
        Requirement(dep).name for dep in project["optional-dependencies"]["stt-whisper"]
    }


def test_starlette_server_pins_and_lock_exclude_cve_2026_48710():
    # BadHost's reviewed fixed boundary is independent of today's exact pin.
    floor = Version("1.0.1")
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    found = set()
    for extra, specs in metadata["project"]["optional-dependencies"].items():
        for requirement in map(Requirement, specs):
            if requirement.name != "starlette":
                continue
            pins = list(requirement.specifier)
            assert len(pins) == 1 and pins[0].operator == "==", (extra, requirement)
            assert Version(pins[0].version) >= floor, (extra, requirement)
            found.add(extra)
    assert {"web", "mcp", "computer-use"} <= found
    dev = [req for req in map(Requirement, metadata["dependency-groups"]["dev"])
           if req.name == "starlette"]
    assert len(dev) == 1
    pins = list(dev[0].specifier)
    assert len(pins) == 1 and pins[0].operator == "==" and Version(pins[0].version) >= floor
    versions = [Version(row["version"]) for row in lock["package"] if row["name"] == "starlette"]
    assert versions and all(version >= floor for version in versions)
