"""Regression test for issue #62723.

The v30→v31 and v31→v32 migrations must not wipe a user's custom
``platforms`` configuration.  Any existing platforms dict must survive
the migration unchanged.

Platforms configuration is NOT in DEFAULT_CONFIG (only display.platforms is),
so strip_defaults in save_config would silently drop it unless the migration
explicitly preserves it.
"""
import os
from unittest.mock import patch

import yaml

from hermes_cli.config import migrate_config, DEFAULT_CONFIG


class TestPlatformsPreservedDuringV30ToV32Migration:
    """Regression test for issue #62723."""

    def test_platforms_survive_v30_to_v31_migration(self, tmp_path):
        """A config with custom platforms on v30 must keep every platform entry intact after v31 migration."""
        config_path = tmp_path / "config.yaml"
        original_platforms = {
            "feishu": {
                "enabled": True,
                "extra": {
                    "app_id": "cli_xxx",
                    "app_secret": "xxx",
                    "admins": ["ou_xxx"],
                },
                "require_mention": True,
            },
            "telegram": {
                "enabled": True,
                "token": "bot_token_here",
            },
        }
        config_path.write_text(
            yaml.safe_dump(
                {
                    "_config_version": 30,
                    "platforms": original_platforms,
                }
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            migrate_config(interactive=False, quiet=True)
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        assert raw["_config_version"] == DEFAULT_CONFIG["_config_version"]
        # Every platform key and every value must be preserved verbatim.
        assert raw["platforms"] == original_platforms

    def test_platforms_survive_v31_to_v32_migration(self, tmp_path):
        """A config with custom platforms on v31 must keep every platform entry intact after v32 migration."""
        config_path = tmp_path / "config.yaml"
        original_platforms = {
            "discord": {
                "enabled": True,
                "token": "discord_bot_token",
                "intents": 32511,
            },
        }
        config_path.write_text(
            yaml.safe_dump(
                {
                    "_config_version": 31,
                    "platforms": original_platforms,
                }
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            migrate_config(interactive=False, quiet=True)
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        assert raw["_config_version"] == DEFAULT_CONFIG["_config_version"]
        # Every platform key and every value must be preserved verbatim.
        assert raw["platforms"] == original_platforms

    def test_platforms_survive_full_v30_to_v32_migration(self, tmp_path):
        """A config with custom platforms on v30 must keep every platform entry intact after full migration to latest."""
        config_path = tmp_path / "config.yaml"
        original_platforms = {
            "feishu": {
                "enabled": True,
                "extra": {
                    "app_id": "cli_xxx",
                    "app_secret": "xxx",
                    "admins": ["ou_xxx"],
                },
                "require_mention": True,
            },
        }
        config_path.write_text(
            yaml.safe_dump(
                {
                    "_config_version": 30,
                    "model": {
                        "default": "deepseek-v4-pro",
                        "provider": "deepseek",
                    },
                    "platforms": original_platforms,
                }
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            migrate_config(interactive=False, quiet=True)
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        assert raw["_config_version"] == DEFAULT_CONFIG["_config_version"]
        # Every platform key and every value must be preserved verbatim.
        assert raw["platforms"] == original_platforms