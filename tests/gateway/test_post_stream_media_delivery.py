"""Post-stream media delivery is explicit-only (#20834).

``GatewayRunner._deliver_media_from_response`` runs AFTER streaming has sent
the visible reply. At that point a bare local filesystem path in the response
text is either text the user already saw, or stale inspected/tool content —
it is NOT an attachment request. Only explicit ``MEDIA:`` directives may
trigger post-stream uploads.

The non-streaming path (``gateway/platforms/base.py``) keeps its bare-path
auto-detect (``extract_local_files``) — that path controls what text is sent
and can strip the path from the visible reply, so auto-attach is intentional
there. This file pins the asymmetry.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import unquote

import pytest

from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _event():
    source = SessionSource(
        platform=Platform.SLACK,
        chat_id="C123CHAN",
        chat_type="group",
        thread_id=None,
    )
    return MessageEvent(
        text="hi",
        message_type=MessageType.TEXT,
        source=source,
        message_id="171.000001",
    )


def _fake_runner(thread_meta):
    return SimpleNamespace(
        _thread_metadata_for_source=lambda source, anchor=None: thread_meta,
        _reply_anchor_for_event=lambda event: None,
    )


def _adapter():
    return SimpleNamespace(
        name="test",
        extract_media=BasePlatformAdapter.extract_media,
        extract_images=BasePlatformAdapter.extract_images,
        extract_local_files=BasePlatformAdapter.extract_local_files,
        send_voice=AsyncMock(return_value=SendResult(success=True, message_id="voice")),
        send_document=AsyncMock(return_value=SendResult(success=True, message_id="doc")),
        send_image_file=AsyncMock(return_value=SendResult(success=True, message_id="image")),
        send_video=AsyncMock(return_value=SendResult(success=True, message_id="video")),
        send_multiple_images=AsyncMock(return_value=SendResult(success=True, message_id="imgs")),
    )


def _allowed_media_path(tmp_path, monkeypatch, name):
    root = tmp_path / "media-cache"
    media_file = root / name
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"media")
    monkeypatch.setattr(
        "gateway.platforms.base.MEDIA_DELIVERY_SAFE_ROOTS",
        (root,),
    )
    return media_file.resolve()


@pytest.mark.asyncio
async def test_bare_local_path_in_streamed_reply_is_not_uploaded(tmp_path, monkeypatch):
    """The #20834 shape: visible reply contains a bare path (from inspected
    content), no MEDIA: directive — nothing may be uploaded post-stream."""
    media_file = _allowed_media_path(tmp_path, monkeypatch, "mockup.png")
    adapter = _adapter()

    await GatewayRunner._deliver_media_from_response(
        _fake_runner({}),
        f"The design lives at {media_file} if you want to look later.",
        _event(),
        adapter,
    )

    adapter.send_multiple_images.assert_not_awaited()
    adapter.send_image_file.assert_not_awaited()
    adapter.send_document.assert_not_awaited()
    adapter.send_video.assert_not_awaited()
    adapter.send_voice.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_media_tag_still_delivers_post_stream(tmp_path, monkeypatch):
    """Explicit MEDIA: directives keep working after the #20834 fix."""
    media_file = _allowed_media_path(tmp_path, monkeypatch, "chart.png")
    adapter = _adapter()

    await GatewayRunner._deliver_media_from_response(
        _fake_runner({}),
        f"Here is the chart.\nMEDIA:{media_file}",
        _event(),
        adapter,
    )

    adapter.send_multiple_images.assert_awaited_once()
    images_kwargs = adapter.send_multiple_images.await_args.kwargs
    assert images_kwargs["chat_id"] == "C123CHAN"
    assert str(media_file) in unquote(images_kwargs["images"][0][0])


@pytest.mark.asyncio
async def test_failed_sendresult_notifies_user_post_stream(tmp_path, monkeypatch):
    """A refused upload (SendResult.success=False) must not be silent: the streamed text
    already promised the file, so each failed attachment gets the same failure notice
    the non-streaming path sends (#141585)."""
    media_file = _allowed_media_path(tmp_path, monkeypatch, "vector.svg")
    adapter = _adapter()
    adapter._notify_media_delivery_failure = AsyncMock()
    adapter.send_document = AsyncMock(return_value=SendResult(success=False, error="rejected"))

    await GatewayRunner._deliver_media_from_response(
        _fake_runner({}),
        f"Here is the file.\nMEDIA:{media_file}",
        _event(),
        adapter,
    )

    adapter.send_document.assert_awaited_once()
    adapter._notify_media_delivery_failure.assert_awaited_once()
    kwargs = adapter._notify_media_delivery_failure.await_args.kwargs
    assert kwargs["chat_id"] == "C123CHAN"
    assert kwargs["media_path"] == str(media_file)
    assert kwargs["is_voice"] is False


@pytest.mark.asyncio
async def test_failed_image_batch_notifies_user_post_stream(tmp_path, monkeypatch):
    """A failed post-stream image batch gets one failure notice per image, not silence."""
    media_file = _allowed_media_path(tmp_path, monkeypatch, "chart.png")
    adapter = _adapter()
    adapter._notify_media_delivery_failure = AsyncMock()
    adapter.send_multiple_images = AsyncMock(return_value=SendResult(success=False, error="too large"))

    await GatewayRunner._deliver_media_from_response(
        _fake_runner({}),
        f"Here is the chart.\nMEDIA:{media_file}",
        _event(),
        adapter,
    )

    adapter.send_multiple_images.assert_awaited_once()
    adapter._notify_media_delivery_failure.assert_awaited_once()
    kwargs = adapter._notify_media_delivery_failure.await_args.kwargs
    assert kwargs["chat_id"] == "C123CHAN"
    assert kwargs["media_path"] == str(media_file)


@pytest.mark.asyncio
async def test_successful_sendresult_needs_no_notice_post_stream(tmp_path, monkeypatch):
    """Successful uploads must not grow spurious failure notices."""
    media_file = _allowed_media_path(tmp_path, monkeypatch, "chart.png")
    adapter = _adapter()
    adapter._notify_media_delivery_failure = AsyncMock()

    await GatewayRunner._deliver_media_from_response(
        _fake_runner({}),
        f"Here is the chart.\nMEDIA:{media_file}",
        _event(),
        adapter,
    )

    adapter._notify_media_delivery_failure.assert_not_awaited()


