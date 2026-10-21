"""Constituent metadata on merged inbound text batches (#142499).

``_enqueue_text_event`` merges separate user messages that arrive within the quiet
period into one ``MessageEvent`` and keeps only the opening message's id. Journaling
hooks then see one record for N messages, and an edit of message #2..#N arrives with
an id nobody recorded. The merge must carry every constituent — ``message_id``, text
boundary, timestamp — on the dispatched event's ``metadata["batched_parts"]``; a
single-message batch is dispatched untouched (no key).
"""

import asyncio
from datetime import datetime
from typing import Any

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter
from gateway.platforms.event import MessageEvent, MessageType
from gateway.session import SessionSource


class _Adapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True), Platform.TELEGRAM)
        self._text_batch_delay_seconds = 0.0
        self._text_batch_split_delay_seconds = 0.0
        self.dispatched: list[MessageEvent] = []

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def send(self, *a: Any, **k: Any) -> None:
        pass

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        return {}

    async def handle_message(self, event: MessageEvent) -> None:
        self.dispatched.append(event)


def _event(text: str, message_id: str, ts: datetime) -> MessageEvent:
    return MessageEvent(
        text=text, message_type=MessageType.TEXT, message_id=message_id, timestamp=ts,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="c", chat_type="dm"))


@pytest.mark.asyncio
async def test_merged_batch_records_every_constituent():
    """Three independent messages merge into one event that still carries each one's id,
    original text and timestamp, in arrival order."""
    adapter = _Adapter()
    t1 = datetime(2026, 10, 21, 10, 0, 1)
    t2 = datetime(2026, 10, 21, 10, 0, 2)
    t3 = datetime(2026, 10, 21, 10, 0, 3)
    adapter._enqueue_text_event(_event("msg1", "41", t1))
    adapter._enqueue_text_event(_event("msg2", "42", t2))
    adapter._enqueue_text_event(_event("msg3", "43", t3))
    await asyncio.gather(*adapter._pending_text_batch_tasks.values())

    dispatched = adapter.dispatched[0]
    # The merged event itself keeps the opening id and the joined text (unchanged).
    assert dispatched.message_id == "41"
    assert dispatched.text == "msg1\nmsg2\nmsg3"
    assert dispatched.metadata["batched_parts"] == [
        {"message_id": "41", "text": "msg1", "timestamp": t1},
        {"message_id": "42", "text": "msg2", "timestamp": t2},
        {"message_id": "43", "text": "msg3", "timestamp": t3},
    ]


@pytest.mark.asyncio
async def test_single_message_batch_has_no_batched_parts():
    """A message dispatched alone is not a batch: no key, zero overhead."""
    adapter = _Adapter()
    adapter._enqueue_text_event(_event("hello", "41", datetime(2026, 10, 21, 10, 0, 1)))
    await asyncio.gather(*adapter._pending_text_batch_tasks.values())

    assert len(adapter.dispatched) == 1
    assert "batched_parts" not in adapter.dispatched[0].metadata
