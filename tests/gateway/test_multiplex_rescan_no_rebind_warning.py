"""A changed profile whose platforms are all already live must say the re-scan rebound nothing."""

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.run import GatewayRunner


def _runner_serving_discord(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    profile = home / "profiles" / "worker"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        "hermes_cli.profiles.get_active_profile_name", lambda: "default"
    )
    (profile / "config.yaml").write_text("model: {default: test}\n", encoding="utf-8")
    (profile / ".env").write_text("DISCORD_BOT_TOKEN=discord-test\n", encoding="utf-8")
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True)
    runner._running = True
    runner._primary_profile_name = "default"
    (
        runner.adapters,
        runner._profile_adapters,
        runner._failed_platforms,
        runner._profile_failed_platforms,
    ) = {}, {}, {}, {}
    runner.pairing_store, runner.pairing_stores = MagicMock(), {}
    runner._busy_text_modes_by_profile, runner._busy_input_modes_by_profile = {}, {}
    runner._register_config_hooks = lambda *a, **kw: None
    runner._configure_profile_adapter = lambda *a: None
    runner._sync_voice_mode_state_to_adapter = lambda *a: None
    runner._restore_secondary_completion_ledgers = lambda *a: None
    runner._adapter_credential_claim = lambda *a: None
    runner._adapter_listener_claim = lambda *a: None
    runner._create_adapter = lambda platform, config: SimpleNamespace(platform=platform)
    connected = []

    async def connect(adapter, platform):
        connected.append(platform)
        return True

    async def after_added(profiles):
        pass

    runner._connect_initial_adapter_with_timeout = connect
    runner._after_profiles_added = after_added
    runner._note_served_profiles([("default", home)])
    return runner, profile, connected


@pytest.mark.asyncio
async def test_rescan_of_fully_live_profile_warns_change_did_not_bind(tmp_path, monkeypatch, caplog):
    runner, profile, connected = _runner_serving_discord(tmp_path, monkeypatch)
    await runner.reconcile_served_profiles()
    assert connected == [Platform.DISCORD]
    assert set(runner._profile_adapters["worker"]) == {Platform.DISCORD}
    # Every platform of the served profile is already live: the re-scan stores the new
    # config but rebuilds nothing, so the live adapter keeps its pre-change settings.
    (profile / "config.yaml").write_text(
        "model: {default: test}\ndiscord: {reactions: false}\n", encoding="utf-8"
    )
    with caplog.at_level(logging.INFO, logger="gateway.run_profile_reconcile"):
        result = await runner.reconcile_served_profiles()
    assert connected == [Platform.DISCORD]  # never a second poller on the same bot
    assert result["rescanned"] == ["worker"]
    warnings = [
        r.getMessage() for r in caplog.records
        if r.levelno == logging.WARNING and "pre-change settings" in r.getMessage()
    ]
    assert warnings and "restart the gateway" in warnings[0]
    assert "discord" in warnings[0]


@pytest.mark.asyncio
async def test_rescan_connecting_a_new_platform_stays_informational(tmp_path, monkeypatch, caplog):
    runner, profile, connected = _runner_serving_discord(tmp_path, monkeypatch)
    await runner.reconcile_served_profiles()
    assert connected == [Platform.DISCORD]
    # A new credential enables a platform that is not live yet: the re-scan does build an
    # adapter, so the outcome is a plain informational line, not the no-rebind warning.
    (profile / ".env").write_text(
        "DISCORD_BOT_TOKEN=discord-test\nTELEGRAM_BOT_TOKEN=telegram-test\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.INFO, logger="gateway.run_profile_reconcile"):
        await runner.reconcile_served_profiles()
    assert connected == [Platform.DISCORD, Platform.TELEGRAM]
    assert not [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "pre-change settings" in r.getMessage()
    ]
