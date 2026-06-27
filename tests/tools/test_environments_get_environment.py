"""Regression tests for tools.environments.get_environment.

Issue #53667: prompt_builder._probe_remote_backend imports ``get_environment``
from ``tools.environments``, but the function was missing — only
``BaseEnvironment`` was exported.  The import failure was silently caught,
causing the backend probe to return ``None`` for every non-local backend.
"""

from __future__ import annotations

import pytest


class TestGetEnvironmentImport:
    """get_environment must be importable from tools.environments."""

    def test_import_succeeds(self):
        from tools.environments import get_environment  # noqa: F401

    def test_import_matches_prompt_builder_path(self):
        """Mirror the exact import in agent/prompt_builder.py:930."""
        from tools.environments import get_environment  # type: ignore

        assert callable(get_environment)

    def test_base_environment_still_exported(self):
        from tools.environments import BaseEnvironment  # noqa: F401

    def test_all_exports(self):
        import tools.environments as te

        assert "get_environment" in te.__all__
        assert "BaseEnvironment" in te.__all__


class TestGetEnvironmentLocal:
    """get_environment with local backend config."""

    def test_creates_local_environment(self):
        from tools.environments import get_environment

        config = {
            "env_type": "local",
            "cwd": "/tmp",
            "timeout": 10,
            "local_persistent": False,
        }
        env = get_environment(config)
        assert hasattr(env, "execute")

    def test_local_environment_executes_command(self):
        from tools.environments import get_environment

        config = {
            "env_type": "local",
            "cwd": "/tmp",
            "timeout": 10,
            "local_persistent": False,
        }
        env = get_environment(config)
        result = env.execute("echo regression-test-53667", timeout=5)
        assert result["returncode"] == 0
        assert "regression-test-53667" in result["output"]

    def test_backward_compatible_with_get_env_config(self):
        """get_environment must accept the dict from _get_env_config()."""
        from tools.environments import get_environment
        from tools.terminal_tool import _get_env_config

        config = _get_env_config()
        env = get_environment(config)
        result = env.execute("echo probe-ok", timeout=5)
        assert result["returncode"] == 0
        assert "probe-ok" in result["output"]


class TestGetEnvironmentConfigAssembly:
    """Verify the config-to-kwargs mapping for non-local backends.

    We mock _create_environment to avoid actually starting Docker/SSH/etc.
    The mock intercepts the call at tools.terminal_tool._create_environment
    (the lazy import inside get_environment resolves to this).
    """

    def test_docker_config_assembly(self, monkeypatch):
        """Verify docker config dict is correctly unpacked."""
        import tools.terminal_tool as tt
        from tools.environments import get_environment

        _captured = {}

        def fake_create(**kwargs):
            _captured.update(kwargs)
            raise RuntimeError("mock: docker not available")

        monkeypatch.setattr(tt, "_create_environment", fake_create)

        config = {
            "env_type": "docker",
            "docker_image": "python:3.11-slim",
            "cwd": "/root",
            "timeout": 30,
            "container_cpu": 2,
            "container_memory": 4096,
            "container_disk": 10240,
            "container_persistent": True,
            "modal_mode": "auto",
            "docker_volumes": ["/data:/data"],
            "docker_mount_cwd_to_workspace": False,
            "docker_forward_env": ["API_KEY"],
            "docker_env": {"FOO": "bar"},
            "docker_run_as_host_user": False,
            "docker_extra_args": ["--network=host"],
            "docker_persist_across_processes": True,
            "docker_orphan_reaper": True,
            "host_cwd": None,
        }
        with pytest.raises(RuntimeError, match="mock"):
            get_environment(config)

        assert _captured["env_type"] == "docker"
        assert _captured["image"] == "python:3.11-slim"
        assert _captured["cwd"] == "/root"
        assert _captured["timeout"] == 30
        assert _captured["container_config"]["container_cpu"] == 2
        assert _captured["container_config"]["docker_volumes"] == ["/data:/data"]

    def test_ssh_config_assembly(self, monkeypatch):
        import tools.terminal_tool as tt
        from tools.environments import get_environment

        _captured = {}

        def fake_create(**kwargs):
            _captured.update(kwargs)
            raise RuntimeError("mock: ssh not available")

        monkeypatch.setattr(tt, "_create_environment", fake_create)

        config = {
            "env_type": "ssh",
            "cwd": "~",
            "timeout": 30,
            "ssh_host": "example.com",
            "ssh_user": "testuser",
            "ssh_port": 2222,
            "ssh_key": "/home/test/.ssh/id_rsa",
            "ssh_persistent": True,
        }
        with pytest.raises(RuntimeError, match="mock"):
            get_environment(config)

        assert _captured["env_type"] == "ssh"
        assert _captured["ssh_config"]["host"] == "example.com"
        assert _captured["ssh_config"]["user"] == "testuser"
        assert _captured["ssh_config"]["port"] == 2222
        assert _captured["ssh_config"]["key"] == "/home/test/.ssh/id_rsa"
        assert _captured["ssh_config"]["persistent"] is True

    def test_local_config_assembly(self, monkeypatch):
        import tools.terminal_tool as tt
        from tools.environments import get_environment

        _captured = {}

        def fake_create(**kwargs):
            _captured.update(kwargs)
            raise RuntimeError("mock")

        monkeypatch.setattr(tt, "_create_environment", fake_create)

        config = {
            "env_type": "local",
            "cwd": "/tmp",
            "timeout": 60,
            "local_persistent": True,
        }
        with pytest.raises(RuntimeError, match="mock"):
            get_environment(config)

        assert _captured["env_type"] == "local"
        assert _captured["local_config"]["persistent"] is True
        assert _captured["timeout"] == 60
