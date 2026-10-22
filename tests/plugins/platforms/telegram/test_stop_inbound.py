"""Drain-time inbound stop (#143122): the gateway drain stops the fetcher while the message
handler is still installed, so updates already fetched (offset acked — no redelivery) are
admitted instead of hitting the handler-less drop in the disconnect gap."""

from types import SimpleNamespace

import pytest

from gateway.config import Platform, PlatformConfig


def _adapter(*, webhook_mode=False, updater_running=True, stop_error=None):
    from plugins.platforms.telegram.adapter import TelegramAdapter

    calls = []

    async def _stop():
        calls.append("stop")
        if stop_error is not None:
            raise stop_error

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(enabled=True, token="***", extra={})
    adapter._webhook_mode = webhook_mode
    adapter._polling_teardown_started = False
    adapter._polling_progress_accepting = True
    adapter._polling_generation = 3
    adapter._polling_progress_event = None
    adapter._app = SimpleNamespace(
        updater=SimpleNamespace(running=updater_running, stop=_stop))
    adapter._stop_calls = calls
    return adapter


@pytest.mark.asyncio
async def test_stop_inbound_fences_and_stops_a_running_updater():
    adapter = _adapter()
    await adapter.stop_inbound()
    assert adapter._stop_calls == ["stop"]
    assert adapter._polling_teardown_started is True
    assert adapter._polling_progress_accepting is False
    assert adapter._polling_generation == 4  # fence generation bump kills in-flight verifiers


@pytest.mark.asyncio
async def test_stop_inbound_is_a_noop_when_already_torn_down():
    adapter = _adapter()
    adapter._polling_teardown_started = True
    await adapter.stop_inbound()
    assert adapter._stop_calls == []


@pytest.mark.asyncio
async def test_stop_inbound_is_a_noop_in_webhook_mode():
    # Webhook updates are server-pushed; there is no fetcher to stop and the handler-less
    # drop window does not apply (Telegram redelivers failed webhook deliveries).
    adapter = _adapter(webhook_mode=True)
    await adapter.stop_inbound()
    assert adapter._stop_calls == []
    assert adapter._polling_teardown_started is False


@pytest.mark.asyncio
async def test_stop_inbound_is_a_noop_when_updater_not_running():
    adapter = _adapter(updater_running=False)
    await adapter.stop_inbound()
    assert adapter._stop_calls == []


@pytest.mark.asyncio
async def test_stop_inbound_swallows_a_wedged_stop():
    # A CLOSE-WAIT socket can wedge updater.stop(); the drain must not block on it —
    # the later disconnect() repeats the bounded stop.
    adapter = _adapter(stop_error=OSError("CLOSE-WAIT wedge"))
    await adapter.stop_inbound()  # must not raise
    assert adapter._stop_calls == ["stop"]
    assert adapter._polling_teardown_started is True


@pytest.mark.asyncio
async def test_fenced_adapter_refuses_to_restart_polling():
    # The heartbeat/reconnect ladder must read the drain-time stop as intentional teardown,
    # not a wedged fetcher to resurrect: a fenced adapter aborts a new polling generation.
    from plugins.platforms.telegram.adapter import _PollingLifecycleAbort

    adapter = _adapter()
    await adapter.stop_inbound()
    app = adapter._app
    with pytest.raises(_PollingLifecycleAbort):
        await adapter._start_polling_once(
            app, drop_pending_updates=False, error_callback=None)
