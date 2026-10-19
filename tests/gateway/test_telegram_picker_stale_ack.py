"""A picker tap past Telegram's callback answer window ("Query is too old") must still act and
still give feedback: the refused toast degrades to a card edit instead of aborting the tap
(#141587)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter
from telegram.error import BadRequest


def _make_adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


def _stale_query(data: str, reply_markup=None):
    """A tap Telegram will no longer let us answer (BadRequest on every ack attempt)."""
    query = AsyncMock()
    query.data = data
    query.answer = AsyncMock(
        side_effect=BadRequest("Query is too old and response timeout expired or query id is invalid"))
    query.message = SimpleNamespace(chat_id=12345, reply_markup=reply_markup)
    query.edit_message_text = AsyncMock()
    return query


class TestStalePickerAck:
    @pytest.mark.asyncio
    async def test_expired_state_notice_falls_back_to_card_edit(self):
        adapter = _make_adapter()
        markup = object()
        query = _stale_query("mm:0", reply_markup=markup)

        await adapter._handle_model_picker_callback(query, "mm:0", "12345")

        query.edit_message_text.assert_awaited_once()
        kwargs = query.edit_message_text.await_args.kwargs
        assert kwargs["reply_markup"] is markup  # edits still work — the card stays usable
        assert kwargs["text"].strip()  # the expired notice is visible on the card itself

    @pytest.mark.asyncio
    async def test_confirmed_switch_still_switches_and_releases_state(self):
        adapter = _make_adapter()
        on_selected = AsyncMock(return_value="Switched to bigmodel")
        adapter._model_picker_state["12345"] = {
            "model_list": ["bigmodel"], "selected_provider": "prov", "providers": [],
            "session_key": "s", "on_model_selected": on_selected,
            "current_model": "m", "current_provider": "prov", "msg_id": 7,
        }
        query = _stale_query("mc:0")

        await adapter._handle_model_picker_callback(query, "mc:0", "12345")

        on_selected.assert_awaited_once_with("12345", "bigmodel", "prov")
        kwargs = query.edit_message_text.await_args.kwargs
        assert "Switched to" in kwargs["text"]
        assert "12345" not in adapter._model_picker_state  # released despite the refused ack

    @pytest.mark.asyncio
    async def test_page_navigation_edit_survives_refused_ack(self):
        adapter = _make_adapter()
        adapter._model_picker_state["12345"] = {
            "providers": [{"slug": "prov", "name": "Prov", "total_models": 12, "is_current": True}],
            "model_list": [f"m{i}" for i in range(12)], "selected_provider": "prov",
            "selected_provider_name": "Prov", "session_key": "s", "on_model_selected": AsyncMock(),
            "current_model": "m", "current_provider": "prov", "msg_id": 7,
        }
        query = _stale_query("mg:1")

        await adapter._handle_model_picker_callback(query, "mg:1", "12345")

        query.edit_message_text.assert_awaited_once()  # page 2 rendered; only the ack was dropped

    @pytest.mark.asyncio
    async def test_choice_picker_expired_notice_falls_back_to_card_edit(self):
        adapter = _make_adapter()
        markup = object()
        query = _stale_query("cp:0", reply_markup=markup)

        await adapter._handle_choice_picker_callback(query, "cp:0", "12345")

        query.edit_message_text.assert_awaited_once()
        assert query.edit_message_text.await_args.kwargs["reply_markup"] is markup

    @pytest.mark.asyncio
    async def test_fresh_ack_still_toasts_without_card_edit(self):
        adapter = _make_adapter()
        query = _stale_query("mm:0")
        query.answer = AsyncMock()  # the tap is fresh: the ack goes through
        query.edit_message_text = AsyncMock()

        await adapter._handle_model_picker_callback(query, "mm:0", "12345")

        query.answer.assert_awaited_once()
        assert query.answer.await_args.kwargs["text"]
        query.edit_message_text.assert_not_awaited()  # no fallback edit on a healthy ack
