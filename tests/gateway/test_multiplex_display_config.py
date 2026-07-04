"""Regression: _load_gateway_config reads per-profile config under multiplex.

When ``_profile_runtime_scope`` sets a ``hermes_home`` override (as it does
for every inbound message under ``multiplex_profiles: true``),
``_load_gateway_config()`` must read the *profile's* ``config.yaml``, not
the root one.

Issue: https://github.com/NousResearch/hermes-agent/issues/58395
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


class TestLoadGatewayConfigProfileAware:
    """_load_gateway_config() respects hermes_home override."""

    def test_reads_profile_config_when_override_active(self, tmp_path, monkeypatch):
        """Under profile scope, display settings come from profile config."""
        root_home = tmp_path / "root"
        root_home.mkdir()
        (root_home / "config.yaml").write_text(
            textwrap.dedent("""\
                display:
                  tool_progress: all
                  interim_assistant_messages: true
            """),
            encoding="utf-8",
        )

        profile_home = tmp_path / "profiles" / "coder"
        profile_home.mkdir(parents=True)
        (profile_home / "config.yaml").write_text(
            textwrap.dedent("""\
                display:
                  tool_progress: "off"
                  interim_assistant_messages: false
            """),
            encoding="utf-8",
        )

        monkeypatch.setenv("HERMES_HOME", str(root_home))

        import gateway.run as gr

        # Without override: reads root config
        cfg_root = gr._load_gateway_config()
        assert cfg_root["display"]["tool_progress"] == "all"
        assert cfg_root["display"]["interim_assistant_messages"] is True

        # With override (simulating _profile_runtime_scope): reads profile config
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override

        token = set_hermes_home_override(str(profile_home))
        try:
            cfg_profile = gr._load_gateway_config()
            assert cfg_profile["display"]["tool_progress"] == "off"
            assert cfg_profile["display"]["interim_assistant_messages"] is False
        finally:
            reset_hermes_home_override(token)

    def test_falls_back_to_root_when_no_profile_config(self, tmp_path, monkeypatch):
        """Profile without config.yaml returns empty dict (not root config)."""
        root_home = tmp_path / "root"
        root_home.mkdir()
        (root_home / "config.yaml").write_text(
            textwrap.dedent("""\
                display:
                  tool_progress: all
            """),
            encoding="utf-8",
        )

        profile_home = tmp_path / "profiles" / "empty"
        profile_home.mkdir(parents=True)
        # No config.yaml in profile

        monkeypatch.setenv("HERMES_HOME", str(root_home))

        import gateway.run as gr

        from hermes_constants import set_hermes_home_override, reset_hermes_home_override

        token = set_hermes_home_override(str(profile_home))
        try:
            cfg = gr._load_gateway_config()
            # No profile config → empty dict, NOT root config
            assert cfg.get("display") is None
        finally:
            reset_hermes_home_override(token)
