"""Tests for config migration v30→31: prune stale toolset names."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def config_with_stale_toolsets(tmp_path: Path, monkeypatch):
    """Config with 'messaging' in platform_toolsets and agent.enabled_toolsets."""
    config = {
        "_config_version": 30,
        "platform_toolsets": {
            "cli": ["hermes-cli", "messaging"],
            "telegram": ["hermes-telegram", "messaging"],
        },
        "agent": {
            "enabled_toolsets": ["hermes-cli", "messaging"],
        },
        "mcp_servers": {},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Clear any cached config
    from hermes_cli import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_LOAD_CONFIG_CACHE", {})

    return config_path


@pytest.fixture
def config_with_mcp_server_name(tmp_path: Path, monkeypatch):
    """Config where a toolset name matches an MCP server name (should NOT be pruned)."""
    config = {
        "_config_version": 30,
        "platform_toolsets": {
            "cli": ["hermes-cli", "my-mcp-server"],
        },
        "agent": {
            "enabled_toolsets": ["hermes-cli", "my-mcp-server"],
        },
        "mcp_servers": {
            "my-mcp-server": {"command": "npx", "args": ["-y", "my-server"]},
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_cli import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_LOAD_CONFIG_CACHE", {})

    return config_path


@pytest.fixture
def config_clean(tmp_path: Path, monkeypatch):
    """Config with no stale toolsets — migration should be a no-op."""
    config = {
        "_config_version": 30,
        "platform_toolsets": {
            "cli": ["hermes-cli"],
        },
        "agent": {
            "enabled_toolsets": ["hermes-cli"],
        },
        "mcp_servers": {},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_cli import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_LOAD_CONFIG_CACHE", {})

    return config_path


class TestStaleToolsetPruning:
    """Version 30→31 migration prunes invalid toolset names."""

    def test_prunes_messaging_from_platform_toolsets(self, config_with_stale_toolsets):
        from hermes_cli.config import migrate_config

        results = migrate_config(quiet=True)
        raw = yaml.safe_load(config_with_stale_toolsets.read_text(encoding="utf-8"))

        assert raw["_config_version"] == 31
        assert raw["platform_toolsets"]["cli"] == ["hermes-cli"]
        assert raw["platform_toolsets"]["telegram"] == ["hermes-telegram"]
        assert "messaging" not in str(raw["platform_toolsets"])

    def test_prunes_messaging_from_enabled_toolsets(self, config_with_stale_toolsets):
        from hermes_cli.config import migrate_config

        migrate_config(quiet=True)
        raw = yaml.safe_load(config_with_stale_toolsets.read_text(encoding="utf-8"))

        assert raw["agent"]["enabled_toolsets"] == ["hermes-cli"]
        assert "messaging" not in raw["agent"]["enabled_toolsets"]

    def test_preserves_mcp_server_names(self, config_with_mcp_server_name):
        from hermes_cli.config import migrate_config

        migrate_config(quiet=True)
        raw = yaml.safe_load(config_with_mcp_server_name.read_text(encoding="utf-8"))

        assert raw["_config_version"] == 31
        # my-mcp-server should be preserved because it's a configured MCP server
        assert "my-mcp-server" in raw["platform_toolsets"]["cli"]
        assert "my-mcp-server" in raw["agent"]["enabled_toolsets"]

    def test_no_op_when_no_stale_toolsets(self, config_clean):
        from hermes_cli.config import migrate_config

        results = migrate_config(quiet=True)
        raw = yaml.safe_load(config_clean.read_text(encoding="utf-8"))

        assert raw["_config_version"] == 31
        assert raw["platform_toolsets"]["cli"] == ["hermes-cli"]
        assert raw["agent"]["enabled_toolsets"] == ["hermes-cli"]

    def test_prunes_multiple_stale_names(self, tmp_path, monkeypatch):
        """Multiple stale toolset names in one platform."""
        config = {
            "_config_version": 30,
            "platform_toolsets": {
                "cli": ["hermes-cli", "messaging", "nonexistent-toolset"],
            },
            "agent": {},
            "mcp_servers": {},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from hermes_cli import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "_LOAD_CONFIG_CACHE", {})

        from hermes_cli.config import migrate_config

        migrate_config(quiet=True)
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        assert raw["platform_toolsets"]["cli"] == ["hermes-cli"]

    def test_handles_missing_platform_toolsets_key(self, tmp_path, monkeypatch):
        """Config without platform_toolsets — should not crash."""
        config = {
            "_config_version": 30,
            "agent": {
                "enabled_toolsets": ["messaging"],
            },
            "mcp_servers": {},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from hermes_cli import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "_LOAD_CONFIG_CACHE", {})

        from hermes_cli.config import migrate_config

        migrate_config(quiet=True)
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        assert raw["_config_version"] == 31
        assert raw["agent"]["enabled_toolsets"] == []
