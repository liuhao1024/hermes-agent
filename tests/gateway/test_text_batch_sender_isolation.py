"""Text batching keeps per-sender isolation in shared group sessions (#145434).

With ``group_sessions_per_user: false`` the session key has no user part, so the text
batch key used to merge chunks from different senders into one event that kept only the
first sender's source — the second sender's words were attributed to the first sender.
The batch key now carries the canonical participant, mirroring how ``build_session_key``
isolates senders when per-user sessions are enabled.
"""

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.event import MessageEvent

from plugins.platforms.discord.adapter import DiscordAdapter


def _adapter(**extra) -> DiscordAdapter:
    return DiscordAdapter(PlatformConfig(
        enabled=True, token="fixture-token", typing_indicator=False, extra=extra))


def _event(adapter: DiscordAdapter, sender: str, text: str, n: int) -> MessageEvent:
    source = adapter.build_source(chat_id="room", chat_type="group", user_id=sender)
    return MessageEvent(text=text, source=source, message_id=f"m{n}")


@pytest.mark.asyncio
async def test_shared_group_session_batches_per_sender():
    adapter = _adapter(group_sessions_per_user=False)
    adapter._enqueue_text_event(_event(adapter, "42", "mine", 1))
    adapter._enqueue_text_event(_event(adapter, "43", "theirs", 2))

    assert len(adapter._pending_text_batches) == 2
    by_sender = {ev.source.user_id: ev.text for ev in adapter._pending_text_batches.values()}
    assert by_sender == {"42": "mine", "43": "theirs"}


@pytest.mark.asyncio
async def test_shared_group_session_flush_keeps_each_sender(monkeypatch):
    adapter = _adapter(group_sessions_per_user=False)
    dispatched = []

    async def capture(event):
        dispatched.append(event)

    monkeypatch.setattr(adapter, "_dispatch_text_batch", capture)
    adapter._enqueue_text_event(_event(adapter, "42", "mine", 1))
    adapter._enqueue_text_event(_event(adapter, "43", "theirs", 2))
    for key in list(adapter._pending_text_batches):
        await adapter._flush_text_batch_now(key)

    assert [(ev.source.user_id, ev.text) for ev in dispatched] == [("42", "mine"), ("43", "theirs")]


@pytest.mark.asyncio
async def test_same_sender_chunks_still_coalesce():
    adapter = _adapter(group_sessions_per_user=False)
    adapter._enqueue_text_event(_event(adapter, "42", "part one", 1))
    adapter._enqueue_text_event(_event(adapter, "42", "part two", 2))

    assert len(adapter._pending_text_batches) == 1
    (batched,) = adapter._pending_text_batches.values()
    assert batched.source.user_id == "42"
    assert "part one" in batched.text and "part two" in batched.text
