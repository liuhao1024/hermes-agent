"""The Telegram adapter tells the gateway whether an admitted message was addressed to the bot.

``MessageEvent.reply_expected`` decides whether a bare silence marker may stand
(tests/gateway/test_gateway_silence_tokens.py); ``silence_allowed`` rejects the marker on user
turns unless the adapter says the message was not addressed to it (#142024). Slack already carries
the flag (tests/gateway/test_slack_reply_expected.py); these tests pin the Telegram mirror: a
free_response chat/topic or a ``require_mention``-off group admitted an unaddressed message as
``False`` (the agents' ``[SILENT]`` contract), while DMs and every addressed trigger keep ``True``
so the visible fallback stays.
"""

from types import SimpleNamespace

from gateway.platforms.event import MessageType

from tests.gateway.test_telegram_group_gating import (
    _bot_command_entity,
    _dm_message,
    _group_message,
    _make_adapter,
    _mention_entity,
)


def _event(adapter, message, msg_type=MessageType.TEXT):
    return adapter._build_message_event(message, msg_type, update_id=2001)


def test_dm_is_addressed_by_construction():
    adapter = _make_adapter()
    assert _event(adapter, _dm_message("hi")).reply_expected is True


def test_mention_entity_is_addressed():
    adapter = _make_adapter(require_mention=True)
    text = "@hermes_bot what's up?"
    message = _group_message(text, entities=[_mention_entity(text)])
    assert _event(adapter, message).reply_expected is True


def test_reply_to_bot_is_addressed():
    adapter = _make_adapter(require_mention=True)
    assert (
        _event(adapter, _group_message("thanks!", reply_to_bot=True)).reply_expected
        is True
    )


def test_wake_word_hit_is_addressed():
    adapter = _make_adapter(require_mention=True, mention_patterns=[r"\bjarvis\b"])
    assert _event(adapter, _group_message("jarvis, anyone?")).reply_expected is True


def test_plain_command_is_addressed_even_unmentioned():
    # ``/cmd@botname`` counts via the mention check; a bare ``/cmd`` admitted by
    # ``require_mention: false`` is still an explicit ask and must keep the fallback.
    adapter = _make_adapter(require_mention=False)
    message = _group_message("/status")
    assert _event(adapter, message, MessageType.COMMAND).reply_expected is True


def test_free_response_chat_unaddressed_may_stay_silent():
    adapter = _make_adapter(require_mention=True, free_response_chats=["-100"])
    assert _event(adapter, _group_message("side chatter")).reply_expected is False


def test_free_response_chat_mention_still_expects_reply():
    adapter = _make_adapter(require_mention=True, free_response_chats=["-100"])
    text = "@hermes_bot ping"
    message = _group_message(text, entities=[_mention_entity(text)])
    assert _event(adapter, message).reply_expected is True


def test_free_response_topic_unaddressed_may_stay_silent():
    adapter = _make_adapter(require_mention=True, free_response_topics=["-100:7"])
    assert (
        _event(adapter, _group_message("topic chatter", thread_id=7)).reply_expected
        is False
    )


def test_require_mention_off_group_unaddressed_may_stay_silent():
    adapter = _make_adapter(require_mention=False)
    assert _event(adapter, _group_message("anyone here?")).reply_expected is False


def test_guest_mention_is_addressed():
    adapter = _make_adapter(
        require_mention=True, guest_mode=True, allowed_chats=["-200"]
    )
    text = "@hermes_bot question from a guest"
    message = _group_message(text, chat_id=-200, entities=[_mention_entity(text)])
    assert _event(adapter, message).reply_expected is True


def test_command_entity_for_bot_is_addressed():
    adapter = _make_adapter(require_mention=True)
    message = _group_message(
        "/status@hermes_bot",
        entities=[_bot_command_entity("/status@hermes_bot", "/status@hermes_bot")],
    )
    assert _event(adapter, message, MessageType.COMMAND).reply_expected is True


def test_channel_post_keeps_the_visible_fallback():
    # Channels are not group chats under ``_is_group_chat``; nothing knows whether the broadcast
    # was addressed, so the adapter stays out of the way (None semantics via True, unchanged).
    adapter = _make_adapter(require_mention=True)
    channel = SimpleNamespace(
        message_id=50,
        text="broadcast",
        caption=None,
        entities=[],
        caption_entities=[],
        message_thread_id=None,
        chat=SimpleNamespace(id=-300, type="channel", title="News", is_forum=False),
        from_user=None,
        reply_to_message=None,
        date=None,
    )
    assert _event(adapter, channel).reply_expected is True
