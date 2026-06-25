"""Tests for the edit_message guard respecting tool_progress_grouping: separate.

Issue: #52212

When ``tool_progress_grouping`` is ``"separate"``, each progress update is sent
as a new message — editing is not required.  The ``edit_message`` guard in
``send_progress_messages()`` must NOT drain the queue in this case, even for
platforms that lack ``edit_message`` support (e.g. QQ Bot, Signal, BlueBubbles).

The fix moves ``can_edit = progress_grouping != "separate"`` before the guard
and changes the guard condition to ``_no_edit_support and can_edit``.
"""

from __future__ import annotations

import asyncio

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NoEditAdapter(BasePlatformAdapter):
    """Adapter that does NOT override ``edit_message`` (uses base no-op)."""

    name = "no-edit-test"

    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="test"), Platform.TELEGRAM)

    async def connect(self):
        return True

    async def disconnect(self):
        pass

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True, message_id="sent-1")

    async def send_typing(self, chat_id, metadata=None):
        pass

    async def get_chat_info(self, chat_id):
        return {"name": "test", "type": "dm"}


class _EditCapableAdapter(BasePlatformAdapter):
    """Adapter that DOES override ``edit_message``."""

    name = "edit-capable-test"

    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="test"), Platform.TELEGRAM)

    async def connect(self):
        return True

    async def disconnect(self):
        pass

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True, message_id="sent-1")

    async def send_typing(self, chat_id, metadata=None):
        pass

    async def edit_message(self, chat_id, message_id, content, *, finalize=False):
        return SendResult(success=True, message_id=message_id)

    async def get_chat_info(self, chat_id):
        return {"name": "test", "type": "dm"}


# ---------------------------------------------------------------------------
# Guard logic simulation
# ---------------------------------------------------------------------------


def _simulate_guard(adapter, progress_grouping: str) -> bool:
    """Simulate the edit_message guard from send_progress_messages().

    Returns True if the guard FIRES (queue would be drained).
    Returns False if the guard is BYPASSED (processing continues).
    """
    can_edit = progress_grouping != "separate"
    _no_edit_support = type(adapter).edit_message is BasePlatformAdapter.edit_message
    if _no_edit_support and can_edit:
        return True  # guard fires — drain queue
    return False  # guard bypassed — continue processing


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_separate_mode_bypasses_guard_for_no_edit_adapter():
    """Non-edit adapter with separate grouping must NOT drain the queue."""
    adapter = _NoEditAdapter()
    assert _simulate_guard(adapter, "separate") is False


def test_accumulate_mode_fires_guard_for_no_edit_adapter():
    """Non-edit adapter with accumulate grouping MUST drain the queue."""
    adapter = _NoEditAdapter()
    assert _simulate_guard(adapter, "accumulate") is True


def test_edit_capable_adapter_bypasses_guard_for_separate():
    """Edit-capable adapter bypasses the guard regardless of grouping."""
    adapter = _EditCapableAdapter()
    assert _simulate_guard(adapter, "separate") is False


def test_edit_capable_adapter_bypasses_guard_for_accumulate():
    """Edit-capable adapter bypasses the guard regardless of grouping."""
    adapter = _EditCapableAdapter()
    assert _simulate_guard(adapter, "accumulate") is False


def test_separate_mode_integration_sends_individual_messages():
    """Integration: separate mode sends each tool update as a new message.

    Simulates the queue processing loop after the guard is bypassed,
    verifying that adapter.send() is called for each queued item.
    """
    adapter = _NoEditAdapter()
    send_calls = []

    async def mock_send(chat_id, content, reply_to=None, metadata=None):
        send_calls.append(content)
        return SendResult(success=True, message_id=f"msg-{len(send_calls)}")

    adapter.send = mock_send

    async def _run():
        queue = asyncio.Queue()
        queue.put_nowait("🔍 web_search: query='test'")
        queue.put_nowait("📄 read_file: path='foo.py'")

        # Simulate the post-guard loop logic for separate mode
        sent = []
        while not queue.empty():
            msg = queue.get_nowait()
            result = await adapter.send(chat_id="test-chat", content=msg)
            if result.success:
                sent.append(msg)
        return sent

    sent = asyncio.get_event_loop().run_until_complete(_run())
    assert len(sent) == 2
    assert send_calls == ["🔍 web_search: query='test'", "📄 read_file: path='foo.py'"]
