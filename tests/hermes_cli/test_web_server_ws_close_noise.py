"""Tests for the dashboard shielded-future close-noise filter (#141687).

Python 3.14's ``asyncio.shield`` reports the inner task's exception via
``call_exception_handler`` when the outer await was cancelled; uvicorn's legacy
websockets impl shields its close task, so every dashboard client that drops
without a close frame (1005) surfaces as an ERROR "exception in shielded
future" record. The filter collapses exactly that to a debug line.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from hermes_cli.web_server import (
    _install_ws_close_noise_filter,
    _is_shielded_ws_close_noise,
)

websockets = pytest.importorskip("websockets")
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close, CloseCode

_SHIELDED_MESSAGE = "'ConnectionClosedOK exception in shielded future'"


def _closed_ok() -> ConnectionClosedOK:
    # close code 1005: peer went away without a close frame.
    close = Close(CloseCode.NO_STATUS_RCVD, "")
    return ConnectionClosedOK(close, close, True)


def _closed_error() -> ConnectionClosedError:
    close = Close(CloseCode.INTERNAL_ERROR, "keepalive ping timeout")
    return ConnectionClosedError(None, close, None)


def test_shielded_close_ok_is_noise():
    ctx = {"message": _SHIELDED_MESSAGE, "exception": _closed_ok(), "future": object()}
    assert _is_shielded_ws_close_noise(ctx) is True


def test_shielded_close_error_stays_an_error():
    # Keepalive ping timeout / abnormal close is a separate class in #141687;
    # it must keep reaching the default handler.
    ctx = {
        "message": "'ConnectionClosedError exception in shielded future'",
        "exception": _closed_error(),
    }
    assert _is_shielded_ws_close_noise(ctx) is False


def test_close_ok_outside_shield_is_not_noise():
    # Same exception type, different path — must still reach the default handler.
    ctx = {"message": "unhandled exception", "exception": _closed_ok()}
    assert _is_shielded_ws_close_noise(ctx) is False


def test_shielded_other_exception_is_not_noise():
    ctx = {
        "message": "'RuntimeError exception in shielded future'",
        "exception": RuntimeError("boom"),
    }
    assert _is_shielded_ws_close_noise(ctx) is False


def test_no_exception_is_not_noise():
    assert _is_shielded_ws_close_noise({"message": _SHIELDED_MESSAGE}) is False


def test_filter_swallows_noise_and_forwards_real_errors(caplog):
    loop = asyncio.new_event_loop()
    try:
        forwarded: list[dict] = []
        loop.set_exception_handler(lambda _loop, ctx: forwarded.append(ctx))

        _install_ws_close_noise_filter(loop)

        with caplog.at_level(logging.DEBUG, logger="hermes_cli.web_server"):
            loop.call_exception_handler(
                {
                    "message": _SHIELDED_MESSAGE,
                    "exception": _closed_ok(),
                    "future": object(),
                }
            )
        assert forwarded == []
        assert any("suppressed" in r.message for r in caplog.records)

        real_ctx = {
            "message": "'RuntimeError exception in shielded future'",
            "exception": RuntimeError("genuine"),
        }
        loop.call_exception_handler(real_ctx)
        assert forwarded == [real_ctx]
    finally:
        loop.close()


def test_filter_is_idempotent():
    loop = asyncio.new_event_loop()
    try:
        _install_ws_close_noise_filter(loop)
        first = loop.get_exception_handler()
        _install_ws_close_noise_filter(loop)
        assert loop.get_exception_handler() is first
    finally:
        loop.close()


def test_real_py314_shield_path_is_captured():
    """End-to-end on the real 3.14 mechanism: cancel the shield's outer await,
    then fail the inner future — the loop reports it to the exception handler
    exactly like the legacy websockets close task does."""
    captured: list[dict] = []

    async def reproduce() -> None:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, ctx: captured.append(ctx))
        inner: asyncio.Future = loop.create_future()
        shielded = asyncio.shield(inner)
        shielded.cancel()
        await asyncio.sleep(0)
        inner.set_exception(_closed_ok())
        for _ in range(100):
            if captured:
                return
            await asyncio.sleep(0)

    asyncio.run(reproduce())

    assert len(captured) == 1
    ctx = captured[0]
    assert "exception in shielded future" in ctx.get("message", "")
    assert isinstance(ctx.get("exception"), ConnectionClosedOK)
    assert _is_shielded_ws_close_noise(ctx) is True


def test_real_py314_shield_path_with_filter_installed_swallows_noise():
    """With the filter installed ahead of a spy handler, the real shielded
    close (1005) never reaches the spy; an abnormal close still does."""
    seen: list[dict] = []

    async def drive(exception: Exception) -> None:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, ctx: seen.append(ctx))
        _install_ws_close_noise_filter(loop)
        inner: asyncio.Future = loop.create_future()
        shielded = asyncio.shield(inner)
        shielded.cancel()
        await asyncio.sleep(0)
        inner.set_exception(exception)
        for _ in range(100):
            if seen:
                return
            await asyncio.sleep(0)

    asyncio.run(drive(_closed_ok()))
    assert seen == []  # normal disconnect without a close frame: swallowed

    asyncio.run(drive(_closed_error()))
    assert len(seen) == 1  # abnormal close (e.g. keepalive timeout): forwarded
    assert isinstance(seen[0]["exception"], ConnectionClosedError)
