"""A real bootstrap reader pins its generation before GC can select victims."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_bootstrap_lease_survives_selection_change(tmp_path, monkeypatch):
    from pm.environments import install_state_dir, runtime_facts_path, site_packages
    from hermes_cli.runtime_state import collect_generations

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    state = install_state_dir(repo)
    for name in ("first", "second", "unused"):
        venv = state / "environments" / name / "venv"
        venv.mkdir(parents=True)
        # pyvenv.cfg first: the layout keys its site-packages path off the recorded version.
        (venv / "pyvenv.cfg").write_text("version = 3.11")
        site_packages(venv).mkdir(parents=True)
        (venv.parent / ".lease-managed").touch()
    legacy = state / "environments" / "old-unleased"
    legacy.mkdir()

    def select(name):
        environment = state / "environments" / name / "venv"
        runtime_facts_path(repo).write_text(json.dumps({"packages": {"venv": {"environment": str(environment)}}}))
        return environment

    first = select("first")
    code = '''
import sys
from pathlib import Path
from pm.environments import activate_dependencies
activate_dependencies(Path(sys.argv[1]))
print("ready", flush=True)
sys.stdin.readline()
'''
    child = subprocess.Popen([sys.executable, "-c", code, str(repo)], env=dict(os.environ),
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        select("second")
        collect_generations(repo, min_age_seconds=0)
        assert first.is_dir()
        assert not (state / "environments" / "unused").exists()
        assert legacy.is_dir()
    finally:
        child.communicate("done\n", timeout=15)
    assert child.returncode == 0
    collect_generations(repo, min_age_seconds=0)
    assert not first.exists()
    assert (state / "environments" / "second").is_dir()
    assert legacy.is_dir()


def _generation_tree(repo: Path, names: list[str]) -> dict[str, Path]:
    from pm.environments import install_state_dir, runtime_facts_path

    state = install_state_dir(repo)
    made = {}
    for name in names:
        venv = state / "environments" / name / "venv"
        venv.mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("version = 3.11")
        (venv.parent / ".lease-managed").touch()
        made[name] = venv
    runtime_facts_path(repo).write_text(json.dumps(
        {"packages": {"venv": {"environment": str(made[names[0]])}}}))
    return made


def test_config_referenced_generation_survives_gc(tmp_path, monkeypatch):
    """A generation whose path config.yaml hard-codes is pinned like ``selected`` (#141448)."""
    from hermes_cli.runtime_state import collect_generations

    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    pinned_name = "5b591a5884ab4c6a9dee7b3327a4a33e"
    made = _generation_tree(repo, ["selected000000000000000000000000", pinned_name,
                                   "idle0000000000000000000000000000"])
    config = home / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("mcp_servers:\n  scrapling:\n"
                      f"    command: {made[pinned_name]}/bin/scrapling-mcp\n")

    removed = collect_generations(repo, min_age_seconds=0)

    assert removed == [made["idle0000000000000000000000000000"].parent]
    assert made[pinned_name].parent.is_dir()  # the config reference pins it
    assert made["selected000000000000000000000000"].parent.is_dir()

    # The pin follows the reference, not the generation: drop the line and it is reclaimable.
    config.write_text("mcp_servers: {}\n")
    assert collect_generations(repo, min_age_seconds=0) == [made[pinned_name].parent]


def test_profile_config_reference_pins_generation(tmp_path, monkeypatch):
    """A profile's config.yaml is a durable reference too, not just the active home's."""
    from hermes_cli.runtime_state import collect_generations

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    pinned_name = "5b591a5884ab4c6a9dee7b3327a4a33e"
    made = _generation_tree(repo, ["selected000000000000000000000000", pinned_name])
    profile_config = tmp_path / "home" / "profiles" / "work" / "config.yaml"
    profile_config.parent.mkdir(parents=True, exist_ok=True)
    profile_config.write_text(f"tools:\n  path: {made[pinned_name]}/bin/tool\n")

    assert collect_generations(repo, min_age_seconds=0) == []
    assert made[pinned_name].parent.is_dir()
