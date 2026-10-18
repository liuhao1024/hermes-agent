"""`hermes pm install --extra NAME` is the one command every missing-extra hint names."""
from types import SimpleNamespace

import pytest

from pm import install_hint


def test_hint_names_a_command_the_cli_accepts(monkeypatch):
    from pm import cli, runtime
    from pm import install as install_mod

    synced = []
    monkeypatch.setattr(runtime, "is_runtime", lambda: True)
    monkeypatch.setattr(install_mod, "sync_venv", lambda extras, **kwargs: synced.append((list(extras), kwargs)))
    monkeypatch.setattr(cli, "_install_names",
                        lambda names, target=None, **kwargs: 0 if not names else pytest.fail(f"tools installed: {names}"))
    monkeypatch.setattr(install_mod, "activate", lambda **kwargs: [])

    argv = install_hint("anthropic").split()[2:]
    assert cli.main(argv) == 0
    assert synced == [(["anthropic"], {"explicit": True})]


def test_extra_syncs_only_the_named_extras(monkeypatch):
    from pm import cli
    from pm import install as install_mod

    synced = []
    monkeypatch.setattr(install_mod, "sync_venv", lambda extras, **kwargs: synced.append(list(extras)))
    monkeypatch.setattr(install_mod, "activate", lambda **kwargs: [])
    monkeypatch.setattr(cli, "_install_names", lambda names, target=None, **kwargs: 0)
    assert cli.cmd_install(SimpleNamespace(names=[], extra=["otlp", "mcp", "otlp"], target=None, tools_only=False)) == 0
    assert synced == [["otlp", "mcp"]]


def test_cold_runtime_refusal_names_the_extra(monkeypatch, tmp_path):
    from pm import client
    from pm import receipt
    from pm.package import InstallError

    monkeypatch.setattr("pm.install.lazy_installs_allowed", lambda: False)
    monkeypatch.setattr(client, "runtime_environment", dict)
    monkeypatch.setattr("pm.registry.package_definitions", lambda names: [])
    monkeypatch.setattr(client, "runtime_command", lambda *args, **kwargs: (_ for _ in ()).throw(
        InstallError("pm-runtime", "not installed or outdated and lazy installs are disabled")))
    for name in ("begin", "record_refusal", "record_step", "finalize"):
        monkeypatch.setattr(receipt, name, lambda *args, **kwargs: None)

    with pytest.raises(InstallError) as info:
        client._request("sync_venv", {"extras": ["bedrock"], "explicit": False, "repair": False},
                        project_root=tmp_path)
    assert install_hint("bedrock") in info.value.remedy
    assert "bedrock" in info.value.cause


def test_failed_generation_is_removed_not_left_as_ghost(monkeypatch, tmp_path):
    """`pm install --extra` on a failing resolution must surface the error and clean its
    generation: a sealed-source snapshot used to leave one ghost environments/<uuid>
    per attempt (#141278)."""
    from pm.environment import ResolutionConflict
    from pm.packages import Venv

    class StubEnvironment:
        def create(self):
            pass

        def check(self):
            pass

    monkeypatch.setattr("pm.environment.managed_environment", lambda *args, **kwargs: StubEnvironment())
    monkeypatch.setattr("pm.plugins_state.enabled_plugins_ordered", lambda **kwargs: [])

    def explode(*args, **kwargs):
        raise ResolutionConflict("lock", "no solution found")

    monkeypatch.setattr("pm.workspace.lock_and_sync", explode)

    source = tmp_path / "core"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname="core"\nversion="1"\nrequires-python=">=3.11"\n', encoding="utf-8")

    from pm.environments import install_state_dir
    environments = install_state_dir(source) / "environments"

    with pytest.raises(ResolutionConflict):
        Venv(source).apply(["anthropic"], plugin_dirs=[], explicit=True)
    assert not environments.exists() or not any(environments.iterdir()), \
        "a failed attempt must not leave a ghost generation behind"
