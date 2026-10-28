"""Regression tests for #145923: MCP failures must never surface with an empty message.

Two surfaces produced message-less failures:
- a cancelled MCP-loop future raised ``CancelledError`` (a ``BaseException`` whose ``str()`` is
  empty and which ``except Exception`` upstream never names) straight out of
  ``_run_on_mcp_loop``'s poll loop, and
- an ``isError`` result whose server-side exception stringified to nothing reached the model as
  "Error executing tool <name>: " — SDK prefix intact, detail blank — leaving a slow tool, a dead
  one and bad arguments indistinguishable.
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from tools import mcp_tool_loop as _loop
from tools.mcp_tool_handlers import _mcp_error_result_message, _render_call_tool_result


def _error_result(text: str):
    return SimpleNamespace(
        is_error=True, content=[SimpleNamespace(text=text, resource=None)]
    )


class TestCancelledMcpLoopFuture:
    def test_self_cancelling_call_is_surfaced_with_its_wait(self, monkeypatch):
        """A coroutine that dies cancelled must come back as a RuntimeError that says so — not a
        message-less CancelledError escaping past every ``except Exception`` on the way up."""
        from tools import mcp_tool

        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        monkeypatch.setattr(mcp_tool, "_mcp_loop", loop)
        try:

            async def _self_cancelling():
                raise asyncio.CancelledError

            with pytest.raises(RuntimeError, match="cancelled on the MCP loop"):
                _loop._run_on_mcp_loop(_self_cancelling, timeout=5)
        finally:
            monkeypatch.setattr(mcp_tool, "_mcp_loop", None)
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            loop.close()


class TestErrorResultMessage:
    def test_half_empty_detail_gets_the_gap_named(self, caplog):
        """The #145923 shape: the SDK prefix survives, the exception detail is blank."""
        message = _mcp_error_result_message(
            _error_result("Error executing tool sophos_list: "), "sophos"
        )
        assert message.startswith("Error executing tool sophos_list:")
        assert "exception message was empty" in message

    def test_blank_result_says_so(self):
        message = _mcp_error_result_message(_error_result("   "), "s")
        assert message == "MCP tool returned an error with no diagnostic text"

    def test_real_detail_passes_through_unchanged(self):
        message = _mcp_error_result_message(
            _error_result("boom: connection refused"), "s"
        )
        assert message == "boom: connection refused"

    def test_render_layer_wires_the_hint_into_the_tool_error(self):
        payload = json.loads(
            _render_call_tool_result(_error_result("Error executing tool x: "), "srv")
        )
        assert "exception message was empty" in json.dumps(payload)
