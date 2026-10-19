"""MEDIA-only turns on edit transports must not leave a stuck preview (#141584).

While streaming, a partial ``MEDIA:<path>`` tag has no deliverable extension yet, so
``_clean_for_display`` keeps it and it lands in the preview with the cursor.  The turn-final
frame strips the complete tag and reaches ``_send_or_edit`` empty; edit transports get no
finalize frame there, so the preview was neither edited nor removed.  These tests pin the
cleanup: the preview is deleted and the turn is marked delivered (post-stream media delivery
is the turn's only visible output).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig


def _edit_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.send = AsyncMock(return_value=SimpleNamespace(success=True, message_id="msg_new"))
    adapter.edit_message = AsyncMock(return_value=SimpleNamespace(success=True))
    adapter.delete_message = AsyncMock(return_value=None)
    adapter.MAX_MESSAGE_LENGTH = 4096
    adapter.REQUIRES_EDIT_FINALIZE = False
    return adapter


def _consumer(adapter: MagicMock) -> GatewayStreamConsumer:
    config = StreamConsumerConfig(edit_interval=0.01, buffer_threshold=5)
    return GatewayStreamConsumer(adapter, "chat_123", config)


class TestMediaOnlyTurnFinalCleansPreview:
    """The turn-final frame of a MEDIA-only answer deletes the stuck preview."""

    @pytest.mark.asyncio
    async def test_media_only_final_deletes_preview_and_marks_delivered(self):
        """A preview holding the partial tag is deleted on the empty turn-final;
        the delivery flags let post-stream media delivery be the only output."""
        adapter = _edit_adapter()
        consumer = _consumer(adapter)
        # The preview exists from the mid-stream partial tag (no extension yet).
        consumer._message_id = "msg_preview"
        consumer._preview_message_ids = {"msg_preview"}
        consumer._segment_preview_message_ids = {"msg_preview"}

        ok = await consumer._send_or_edit(
            "MEDIA:/opt/data/cache/scratch/report.md", finalize=True, is_turn_final=True)

        assert ok is True
        adapter.delete_message.assert_awaited_once_with("chat_123", "msg_preview")
        assert consumer._message_id is None
        assert consumer._preview_message_ids == set()
        assert consumer._segment_preview_message_ids == set()
        assert consumer._final_response_sent is True
        assert consumer._final_content_delivered is True

    @pytest.mark.asyncio
    async def test_midstream_empty_frame_keeps_preview(self):
        """Cursor-only mid-stream frames are routine; the preview must survive them."""
        adapter = _edit_adapter()
        consumer = _consumer(adapter)
        consumer._message_id = "msg_preview"

        ok = await consumer._send_or_edit("▉", finalize=False, is_turn_final=False)

        assert ok is True
        adapter.delete_message.assert_not_awaited()
        assert consumer._message_id == "msg_preview"
        assert consumer._final_response_sent is False

    @pytest.mark.asyncio
    async def test_cancel_finalize_keeps_preview(self):
        """The cancel path finalizes with is_turn_final=False — its best-effort seal
        must not delete the preview (that is not this cleanup's contract)."""
        adapter = _edit_adapter()
        consumer = _consumer(adapter)
        consumer._message_id = "msg_preview"

        ok = await consumer._send_or_edit(
            "MEDIA:/opt/data/cache/scratch/report.md", finalize=True, is_turn_final=False)

        assert ok is True
        adapter.delete_message.assert_not_awaited()
        assert consumer._message_id == "msg_preview"

    @pytest.mark.asyncio
    async def test_no_preview_no_delete(self):
        """Without an editable preview (draft path, nothing sent yet) the empty
        turn-final is a no-op — delete_message must not be called."""
        adapter = _edit_adapter()
        consumer = _consumer(adapter)
        assert consumer._message_id is None

        ok = await consumer._send_or_edit(
            "MEDIA:/opt/data/cache/scratch/report.md", finalize=True, is_turn_final=True)

        assert ok is True
        adapter.delete_message.assert_not_awaited()
        adapter.send.assert_not_awaited()
