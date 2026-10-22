"""Outbound Matrix reply relations only carry real Matrix event ids.

Desktop-originated turns hand the adapter the session store's internal message
UID (bare 32-char hex, no ``$`` sigil). ``_apply_relation_metadata`` embedded it
verbatim into ``m.relates_to.m.in_reply_to.event_id``, and rust-sdk clients
(Element X) refuse to parse the whole ``m.room.message`` event over the
malformed identifier — the reply text is lost on those clients (#143137).
Only ``$``-sigiled event ids may ride the wire; anything else drops the reply
decoration (cosmetic) instead of the message. Mirrors the sigil guard the Moca
client added on its side for the same class of bad ``in_reply_to`` ids.
"""

import asyncio
from unittest.mock import MagicMock

from gateway.config import PlatformConfig


def _make_adapter():
    """Create a MatrixAdapter with mocked config (no live client)."""
    from plugins.platforms.matrix.adapter import MatrixAdapter

    config = PlatformConfig(
        enabled=True,
        token="syt_test_token",
        extra={
            "homeserver": "https://matrix.example.org",
            "user_id": "@hermes:example.org",
        },
    )
    adapter = MatrixAdapter(config)
    adapter._client = MagicMock()  # truthy: a client is connected
    return adapter


def _sent_content(adapter, *, reply_to=None, metadata=None):
    """Run one outbound send and return the m.room.message content it built."""
    captured = {}

    async def _fake_send(room_id, msg_content):
        captured.update(msg_content)
        return "$sent_evt"

    adapter._send_room_message = _fake_send
    asyncio.run(adapter.send("!room1:example.org", "hello", reply_to=reply_to, metadata=metadata))
    return captured


class TestReplyEventIdSigil:
    """``m.in_reply_to`` never embeds a non-Matrix identifier."""

    def test_matrix_event_id_reply_is_embedded(self):
        adapter = _make_adapter()
        content = _sent_content(adapter, reply_to="$real_evt")
        assert content["m.relates_to"]["m.in_reply_to"]["event_id"] == "$real_evt"

    def test_internal_message_uid_reply_is_not_embedded(self):
        adapter = _make_adapter()
        content = _sent_content(adapter, reply_to="a51ff6d975da4faa9d447297d9fc439a")
        assert "m.relates_to" not in content

    def test_empty_reply_to_stays_absent(self):
        adapter = _make_adapter()
        content = _sent_content(adapter, reply_to="")
        assert "m.relates_to" not in content

    def test_thread_fallback_uses_thread_root_when_reply_is_uid(self):
        adapter = _make_adapter()
        content = _sent_content(
            adapter,
            reply_to="be881dfef2c041c2b90816127fea365d",
            metadata={"thread_id": "$root_evt"},
        )
        relates_to = content["m.relates_to"]
        assert relates_to["rel_type"] == "m.thread"
        assert relates_to["event_id"] == "$root_evt"
        # The reply fallback defaults to the thread root, not the internal UID.
        assert relates_to["m.in_reply_to"]["event_id"] == "$root_evt"

    def test_thread_with_valid_reply_keeps_the_reply_target(self):
        adapter = _make_adapter()
        content = _sent_content(adapter, reply_to="$real_evt", metadata={"thread_id": "$root_evt"})
        relates_to = content["m.relates_to"]
        assert relates_to["m.in_reply_to"]["event_id"] == "$real_evt"
        assert relates_to["event_id"] == "$root_evt"
