"""Tests for TelegramAdapter._merge_into_pending media-list parallelism.

An album (shared ``media_group_id``) and a photo burst both merge their items
through ``_merge_into_pending``. The document branch sets a single-element
``media_text_inlined`` list while photo branches set none, so the merge used to
leave one flag for N URLs — native admission then refused the whole album with
``invalid_params(media_text_inlined_length)`` (#143605), and in the
photo-seeded case the document's ``True`` flag was silently dropped.
"""

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.event import MessageEvent, MessageType
from gateway.session_envelope import _media_clause
from plugins.platforms.telegram.adapter import TelegramAdapter


def _media_event(urls, types, flags, text=None):
    ev = MessageEvent(text=text or "")
    ev.message_type = MessageType.PHOTO if types and types[0].startswith("image/") else MessageType.TEXT
    ev.media_urls = list(urls)
    ev.media_types = list(types)
    ev.media_text_inlined = list(flags)
    return ev


@pytest.fixture()
def adapter():
    return TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))


def _merge_album(adapter, events):
    pending = {}
    for ev in events:
        adapter._merge_into_pending(pending, "album:1", ev)
    return pending["album:1"]


class TestAlbumFlagsStayParallel:
    def test_two_documents(self, adapter):
        merged = _merge_album(adapter, [
            _media_event(["/cache/a.pdf"], ["application/pdf"], [False]),
            _media_event(["/cache/b.txt"], ["text/plain"], [True]),
        ])
        assert merged.media_urls == ["/cache/a.pdf", "/cache/b.txt"]
        assert merged.media_text_inlined == [False, True]
        assert _media_clause(merged) is None

    def test_document_then_photo_pads_missing_flags(self, adapter):
        merged = _merge_album(adapter, [
            _media_event(["/cache/a.pdf"], ["application/pdf"], [False]),
            _media_event(["/cache/img.jpg"], ["image/jpeg"], []),
        ])
        assert merged.media_text_inlined == [False, None]
        assert _media_clause(merged) is None

    def test_photo_then_document_keeps_document_flag(self, adapter):
        # Before the fix this album passed admission but the document's True flag never
        # reached the envelope, so downstream re-read already-inlined content.
        merged = _merge_album(adapter, [
            _media_event(["/cache/img.jpg"], ["image/jpeg"], []),
            _media_event(["/cache/a.txt"], ["text/plain"], [True]),
        ])
        assert merged.media_text_inlined == [None, True]
        assert _media_clause(merged) is None

    def test_two_photos_pad_parallel(self, adapter):
        merged = _merge_album(adapter, [
            _media_event(["/cache/1.jpg"], ["image/jpeg"], []),
            _media_event(["/cache/2.jpg"], ["image/jpeg"], []),
        ])
        assert merged.media_text_inlined == [None, None]
        assert _media_clause(merged) is None

    def test_third_document_second_merge(self, adapter):
        merged = _merge_album(adapter, [
            _media_event(["/cache/a.pdf"], ["application/pdf"], [False]),
            _media_event(["/cache/b.txt"], ["text/plain"], [True]),
            _media_event(["/cache/c.pdf"], ["application/pdf"], [False]),
        ])
        assert merged.media_text_inlined == [False, True, False]
        assert _media_clause(merged) is None


class TestAlbumMergeStillMergesMedia:
    def test_seed_event_is_kept_as_is(self, adapter):
        first = _media_event(["/cache/a.pdf"], ["application/pdf"], [False])
        pending = {}
        adapter._merge_into_pending(pending, "album:1", first)
        assert pending["album:1"] is first

    def test_urls_types_and_captions_merge(self, adapter):
        merged = _merge_album(adapter, [
            _media_event(["/cache/a.pdf"], ["application/pdf"], [False], text="first"),
            _media_event(["/cache/b.pdf"], ["application/pdf"], [False], text="second"),
        ])
        assert merged.media_urls == ["/cache/a.pdf", "/cache/b.pdf"]
        assert merged.media_types == ["application/pdf", "application/pdf"]
        assert "first" in merged.text and "second" in merged.text
