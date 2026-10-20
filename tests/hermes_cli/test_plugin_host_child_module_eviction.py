"""The plugin host child's module eviction must not iterate the live ``sys.modules`` mapping.

``op_unload`` and ``_import_plugin``'s failed-import cleanup run while other threads in the host
process can still import (the ASGI lane, lazy registrations), and iterating the live mapping raises
``RuntimeError: dictionary changed size during iteration`` — the unload itself then fails. Like
``tests/hermes_cli/test_plugin_module_eviction_race.py``, this reproduces the concurrent mutation
deterministically: a private stand-in for ``sys.modules`` gains an entry from a second thread at a
fixed point of the walk.
"""

from __future__ import annotations

import threading
import types

import pytest

from hermes_cli import plugin_host_child


def _switch_point_modules() -> tuple[dict[str, types.ModuleType], threading.Event]:
    """A private mapping whose ``unrelated`` key inserts an entry mid-walk when prefix-tested."""
    modules: dict[str, types.ModuleType] = {}
    handed_off = threading.Event()

    def _insert_elsewhere() -> None:
        modules["race_probe_sdk.client"] = types.ModuleType("race_probe_sdk.client")

    class _SwitchPoint(str):
        """A key whose prefix test runs a thread that inserts into the mapping mid-walk, deterministically."""

        def startswith(self, *args):
            if not handed_off.is_set():
                handed_off.set()
                worker = threading.Thread(target=_insert_elsewhere)
                worker.start()
                worker.join()
            return str.startswith(self, *args)

    for name in ("hermes_plugins.probe", "hermes_plugins.probe.adapter"):
        modules[name] = types.ModuleType(name)
    modules[_SwitchPoint("unrelated")] = types.ModuleType("unrelated")
    modules["hermes_plugins.probe.tools"] = types.ModuleType("hermes_plugins.probe.tools")
    return modules, handed_off


def _bare_runtime() -> plugin_host_child.HostRuntime:
    runtime = plugin_host_child.HostRuntime.__new__(plugin_host_child.HostRuntime)
    runtime.refs = {}
    runtime.owners = {}
    runtime._lock = threading.Lock()
    runtime.unload_callbacks = {}
    runtime.modules = {"probe-plugin": "hermes_plugins.probe"}
    return runtime


def test_op_unload_survives_a_concurrent_mapping_insert(monkeypatch):
    modules, handed_off = _switch_point_modules()
    monkeypatch.setattr(plugin_host_child, "sys", types.SimpleNamespace(modules=modules))
    runtime = _bare_runtime()

    runtime.op_unload({"plugin_key": "probe-plugin"})

    assert handed_off.is_set()
    assert sorted(modules) == ["race_probe_sdk.client", "unrelated"]


def test_import_plugin_cleanup_survives_a_concurrent_mapping_insert(monkeypatch, tmp_path):
    plugin_dir = tmp_path / "probe_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text("raise RuntimeError('plugin boom')\n")
    modules, handed_off = _switch_point_modules()
    monkeypatch.setattr(plugin_host_child, "sys", types.SimpleNamespace(modules=modules))

    # Before the snapshot fix the cleanup's own "dictionary changed size during iteration"
    # RuntimeError replaced the plugin's original failure on the way out.
    with pytest.raises(RuntimeError, match="plugin boom"):
        plugin_host_child._import_plugin({"path": str(plugin_dir), "module_name": "hermes_plugins.probe"})

    assert handed_off.is_set()
    assert "hermes_plugins.probe" not in modules
    assert "race_probe_sdk.client" in modules
