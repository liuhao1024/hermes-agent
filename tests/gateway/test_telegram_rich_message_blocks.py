"""Rich-message block messages must reach the text pipeline instead of vanishing (#146067).

Long / multi-paragraph Telegram messages can arrive with an empty ``Message.text`` and the
content in ``rich_message.blocks`` — a Bot API field python-telegram-bot does not model, so it
survives only in ``Message.api_kwargs``. ``filters.TEXT`` never matches such an update, and the
``not msg.text`` guard in ``_handle_text_message`` dropped the rest: Telegram acknowledged the
update, no handler claimed it, and the message vanished without a log line.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from plugins.platforms.telegram.adapter import _RichMessageFilter
from plugins.platforms.telegram.telegram_entities import (
    expand_link_entities,
    rich_message_text,
)
from tests.gateway.test_telegram_group_gating import _dm_message, _make_adapter

# The payload captured in the issue report (paragraph / empty separator / paragraph).
_ISSUE_PAYLOAD = [
    {"type": "paragraph", "text": "Can't you update the firmware yourself?"},
    {"type": "paragraph", "text": ""},
    {"type": "paragraph", "text": "It's 20 eur. Looks ok in the current market."},
]


def _rich_dm_message(blocks, **kwargs):
    msg = _dm_message(text=None, **kwargs)
    msg.api_kwargs = {"rich_message": {"blocks": blocks}}
    return msg


def test_rich_message_text_flattens_issue_payload():
    msg = _rich_dm_message(_ISSUE_PAYLOAD)
    assert rich_message_text(msg) == (
        "Can't you update the firmware yourself?\n"
        "\n"
        "It's 20 eur. Looks ok in the current market."
    )


def test_expand_link_entities_prefers_rich_message_when_text_empty():
    assert (
        expand_link_entities(_rich_dm_message([{"type": "paragraph", "text": "hello"}]))
        == "hello"
    )


def test_expand_link_entities_still_prefers_plain_text():
    msg = _dm_message(text="plain")
    msg.api_kwargs = {
        "rich_message": {"blocks": [{"type": "paragraph", "text": "rich"}]}
    }
    assert expand_link_entities(msg) == "plain"


def test_list_items_render_as_bullets_and_containers_recurse():
    blocks = [
        {
            "type": "list",
            "items": [
                {"type": "list_item", "text": "first"},
                {"type": "list_item", "text": "second"},
            ],
        },
        {"type": "blockquote", "blocks": [{"type": "paragraph", "text": "quoted"}]},
        "bare string block",
    ]
    assert (
        rich_message_text(_rich_dm_message(blocks))
        == "- first\n- second\nquoted\nbare string block"
    )


def test_malformed_rich_message_yields_nothing():
    assert rich_message_text(_dm_message(text=None)) == ""  # no api_kwargs at all
    msg = _dm_message(text=None)
    msg.api_kwargs = {}
    assert rich_message_text(msg) == ""
    msg.api_kwargs = {"rich_message": {"blocks": "not-a-list"}}
    assert rich_message_text(msg) == ""
    # Blocks that carry no inline text (media etc.) contribute nothing — and the guard in
    # _handle_text_message still drops the message rather than enqueueing an empty event.
    msg.api_kwargs = {"rich_message": {"blocks": [{"type": "image", "file_id": "x"}]}}
    assert rich_message_text(msg) == ""


def test_rich_filter_matches_only_empty_text_messages():
    flt = _RichMessageFilter()
    rich = _rich_dm_message([{"type": "paragraph", "text": "hi"}])
    assert flt.check_update(SimpleNamespace(effective_message=rich)) is True
    assert (
        flt.check_update(SimpleNamespace(effective_message=_dm_message(text="plain")))
        is False
    )
    assert (
        flt.check_update(SimpleNamespace(effective_message=_dm_message(text=None)))
        is False
    )
    assert flt.check_update(SimpleNamespace(effective_message=None)) is False


def test_rich_message_dm_reaches_the_text_pipeline():
    adapter = _make_adapter()
    events = []
    adapter._enqueue_text_event = events.append
    adapter.handle_message = AsyncMock(side_effect=events.append)
    adapter._ensure_forum_commands = AsyncMock()
    adapter._cache_inbound_av = AsyncMock(return_value=False)

    update = SimpleNamespace(
        update_id=901, message=_rich_dm_message(_ISSUE_PAYLOAD), effective_message=None
    )
    # Pre-fix the ``not msg.text`` guard returned here and no event ever fired.
    asyncio.run(adapter._handle_text_message(update, SimpleNamespace()))

    assert len(events) == 1, f"rich-block message was dropped: {events!r}"
    assert "Can't you update the firmware yourself?" in events[0].text
    assert "20 eur" in events[0].text
