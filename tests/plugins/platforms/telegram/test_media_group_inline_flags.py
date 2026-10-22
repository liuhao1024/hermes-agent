"""A Telegram media-group merge must keep ``media_text_inlined`` aligned with ``media_urls``.

Documents set one inline flag per attachment while photos set none, so an album mixing
them (or two documents) left the merged event with mismatched list lengths and native
admission refused the whole album with ``media_text_inlined_length`` (#143001).
"""

from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import SessionSource
from gateway.platforms.event import MessageEvent, MessageType
from gateway.session_envelope import _media_clause
from plugins.platforms.telegram.adapter import TelegramAdapter


def _make_adapter() -> TelegramAdapter:
    adapter = object.__new__(TelegramAdapter)
    adapter._platform = Platform.TELEGRAM
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(enabled=True, token="test-token")
    adapter._set_status_indicator = AsyncMock()
    adapter._release_platform_lock = lambda: None
    return adapter


def _media_event(urls: list, flags: list, message_type: MessageType = MessageType.DOCUMENT,
                 text: str = "") -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=message_type,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm"),
        media_urls=list(urls),
        media_types=["application/octet-stream"] * len(urls),
        media_text_inlined=list(flags),
    )


@pytest.mark.parametrize("first_flags,second_flags,merged_flags", [
    ([False], [False], [False, False]),          # two binary documents
    ([True], [False], [True, False]),            # inlined text doc + binary doc
    ([], [False], [None, False]),                # photo first, document joins
    ([False], [], [False, None]),                # document first, photo joins
    ([], [], [None, None]),                      # photo-only group keeps lengths aligned
])
def test_media_group_merge_aligns_inline_flags(first_flags, second_flags, merged_flags):
    adapter = _make_adapter()
    pending: dict = {}
    adapter._merge_into_pending(pending, "album", _media_event(["/cache/a"], first_flags))
    adapter._merge_into_pending(pending, "album", _media_event(["/cache/b"], second_flags))

    merged = pending["album"]
    assert merged.media_urls == ["/cache/a", "/cache/b"]
    assert merged.media_text_inlined == merged_flags
    assert len(merged.media_text_inlined) == len(merged.media_urls)
    assert _media_clause(merged) is None


def test_media_group_merge_keeps_caption():
    adapter = _make_adapter()
    pending: dict = {}
    adapter._merge_into_pending(pending, "album", _media_event(["/cache/a.docx"], [False], text="first part"))
    adapter._merge_into_pending(pending, "album", _media_event(["/cache/b.docx"], [False], text="second part"))

    assert "first part" in pending["album"].text
    assert "second part" in pending["album"].text
