"""Telegram send() must wait for reconnect instead of dropping the final reply.

A network blip can disconnect Telegram while the agent is sending its final
response. send() used to return "Not connected" immediately (retryable=False),
so the delivery ledger held the answer until the next gateway boot — hours
later. QQBot already waits; these tests pin the same contract on Telegram,
including the reconnect-watcher case that *replaces* the adapter object.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


def _make_adapter() -> TelegramAdapter:
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._rich_send_disabled = True
    adapter.send_typing = AsyncMock()
    adapter._RECONNECT_WAIT_SECONDS = 0.6
    adapter._RECONNECT_POLL_INTERVAL = 0.05
    return adapter


def _connected_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    return bot


@pytest.mark.asyncio
async def test_send_waits_and_succeeds_when_bot_returns():
    adapter = _make_adapter()
    adapter._bot = None

    async def restore_bot() -> None:
        await asyncio.sleep(0.12)
        adapter._bot = _connected_bot()

    asyncio.get_running_loop().create_task(restore_bot())
    result = await adapter.send("123", "hello")

    assert result.success is True
    assert result.message_id == "42"
    adapter._bot.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_send_delegates_to_replacement_adapter_installed_mid_wait():
    old = _make_adapter()
    old._bot = None

    live = _make_adapter()
    live._bot = _connected_bot()
    live._RECONNECT_WAIT_SECONDS = 0.01

    runner = MagicMock()
    runner.adapters = {}
    old.gateway_runner = runner

    async def install_replacement() -> None:
        await asyncio.sleep(0.12)
        runner.adapters[old.platform] = live

    asyncio.get_running_loop().create_task(install_replacement())
    result = await old.send("123", "hello")

    assert result.success is True
    assert result.message_id == "42"
    live._bot.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_send_delegates_immediately_when_replacement_already_live():
    old = _make_adapter()
    old._bot = None
    live = _make_adapter()
    live._bot = _connected_bot()
    runner = MagicMock()
    runner.adapters = {old.platform: live}
    old.gateway_runner = runner
    old._wait_for_reconnection = AsyncMock(
        side_effect=AssertionError("must not wait when replacement is already live")
    )

    result = await old.send("123", "hello")

    assert result.success is True
    live._bot.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_send_timeout_is_retryable_not_connected():
    adapter = _make_adapter()
    adapter._bot = None
    adapter._RECONNECT_WAIT_SECONDS = 0.15
    adapter._RECONNECT_POLL_INTERVAL = 0.05

    result = await adapter.send("123", "hello")

    assert result.success is False
    assert result.error == "Not connected"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_send_permanent_fatal_fails_immediately_without_wait():
    adapter = _make_adapter()
    adapter._bot = None
    adapter._set_fatal_error("telegram_auth_error", "invalid token", retryable=False)
    adapter._wait_for_reconnection = AsyncMock(
        side_effect=AssertionError("must not wait on permanent fatal")
    )

    result = await adapter.send("123", "hello")

    assert result.success is False
    assert result.error == "Not connected"
    assert result.retryable is False


def _make_secondary_runner(profile: str, profile_adapters: dict, primary_adapters: dict) -> MagicMock:
    runner = MagicMock()
    runner.adapters = primary_adapters
    runner._profile_adapters = {profile: profile_adapters}
    return runner


@pytest.mark.asyncio
async def test_send_never_borrows_primary_bot_for_disconnected_secondary():
    """A disconnected secondary must fail closed, not deliver through the default bot (#143364)."""
    stale = _make_adapter()
    stale._hermes_profile_name = "yori"
    stale._bot = None
    stale._RECONNECT_WAIT_SECONDS = 0.15
    stale._RECONNECT_POLL_INTERVAL = 0.05

    primary_bot = _connected_bot()
    primary = _make_adapter()
    primary._bot = primary_bot
    # The default profile's Telegram bot is live while yori's is mid-reconnect.
    stale.gateway_runner = _make_secondary_runner("yori", {}, {stale.platform: primary})

    result = await stale.send("123", "mid-turn progress")

    assert result.success is False
    assert result.error == "Not connected"
    assert result.retryable is True
    primary_bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_delegates_to_own_profile_replacement_installed_mid_wait():
    """A secondary waiting out a reconnect hands off to its own profile's rebuilt adapter."""
    stale = _make_adapter()
    stale._hermes_profile_name = "yori"
    stale._bot = None
    stale._RECONNECT_WAIT_SECONDS = 0.6
    stale._RECONNECT_POLL_INTERVAL = 0.05

    primary_bot = _connected_bot()
    primary = _make_adapter()
    primary._bot = primary_bot
    live = _make_adapter()
    live._bot = _connected_bot()
    live._RECONNECT_WAIT_SECONDS = 0.01
    runner = _make_secondary_runner("yori", {}, {stale.platform: primary})
    stale.gateway_runner = runner

    # Install the own-profile replacement once send() has actually entered its reconnect wait.
    wait_started = asyncio.Event()
    original_wait = stale._wait_for_reconnection

    async def wait_and_mark() -> bool:
        wait_started.set()
        return await original_wait()

    stale._wait_for_reconnection = wait_and_mark

    async def install_own_profile_replacement() -> None:
        await wait_started.wait()
        runner._profile_adapters["yori"][stale.platform] = live

    asyncio.get_running_loop().create_task(install_own_profile_replacement())
    result = await stale.send("123", "hello")

    assert result.success is True
    assert result.message_id == "42"
    live._bot.send_message.assert_awaited()
    primary_bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_delegates_immediately_to_live_own_profile_replacement():
    stale = _make_adapter()
    stale._hermes_profile_name = "yori"
    stale._bot = None
    live = _make_adapter()
    live._bot = _connected_bot()
    primary = _make_adapter()
    primary._bot = _connected_bot()
    stale.gateway_runner = _make_secondary_runner("yori", {stale.platform: live}, {stale.platform: primary})
    stale._wait_for_reconnection = AsyncMock(
        side_effect=AssertionError("must not wait when the own-profile replacement is live")
    )

    result = await stale.send("123", "hello")

    assert result.success is True
    live._bot.send_message.assert_awaited()
    primary._bot.send_message.assert_not_awaited()
