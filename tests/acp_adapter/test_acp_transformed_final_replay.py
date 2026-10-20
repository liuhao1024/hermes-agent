"""A plugin-rewritten final response must not replay the full text on the streamed id (#141919).

ACP clients append same-``messageId`` ``agent_message_chunk`` deltas into one
message, so ``_finish_turn`` re-sending the whole transformed ``final_response``
under the streamed bubble's id made schema-conformant clients render the entire
reply twice. The corrected contract: an appending transform sends only the
suffix under the streamed id; a deeper rewrite opens a fresh id so the
corrected text renders as its own bubble; an unstreamed final response keeps
sending the full text on its own id.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from acp_adapter.events import AssistantMessageIdAllocator
from acp_adapter.server import HermesACPAgent
from acp_adapter.session import SessionState


class _CaptureConn:
    """Records every session_update the turn tail emits."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, object]] = []

    async def session_update(self, session_id, update):
        self.updates.append((session_id, update))


async def _noop_coro(*args, **kwargs):
    return None


def _make_server():
    server = HermesACPAgent(session_manager=SimpleNamespace(save_session=lambda sid: None))

    async def fake_prompt(*, prompt, session_id, **kwargs):
        from acp.schema import PromptResponse

        return PromptResponse(stop_reason="end_turn")

    server.prompt = fake_prompt  # type: ignore[method-assign]
    server._send_usage_update = _noop_coro  # type: ignore[method-assign]
    return server


def _streamed_state(streamed_id: str | None = None) -> SessionState:
    allocator = AssistantMessageIdAllocator()
    if streamed_id is None:
        allocator.current()  # deltas opened the bubble…
        allocator.close()  # …and the end-of-stream flush closed it
    state = SessionState(
        session_id="s1",
        agent=SimpleNamespace(session_id="h1"),
        history=[],
        cancel_event=None,
        is_running=True,
        queued_prompts=[],
        runtime_lock=threading.Lock(),
        current_prompt_text="hello",
    )
    state.message_ids = allocator
    return state


def _finish(server, state, result, streamed_message, streamed_text=""):
    conn = _CaptureConn()
    resp = asyncio.run(
        server._finish_turn(state, "s1", conn, result, "h1", streamed_message, streamed_text)
    )
    return conn, resp


def _chunk_text(update) -> str:
    content = update.content
    return content.text if hasattr(content, "text") else ""


def test_appending_transform_sends_only_suffix_under_streamed_id():
    server = _make_server()
    state = _streamed_state()
    streamed_id = state.message_ids.last()
    result = {"final_response": "Hello\n\n-- signed by plugin", "messages": [], "response_transformed": True}

    conn, _ = _finish(server, state, result, streamed_message=True, streamed_text="Hello")

    assert len(conn.updates) == 1
    session_id, update = conn.updates[0]
    assert session_id == "s1"
    assert _chunk_text(update) == "\n\n-- signed by plugin"
    assert update.message_id == streamed_id


def test_deep_rewrite_reopens_a_fresh_message_id():
    server = _make_server()
    state = _streamed_state()
    streamed_id = state.message_ids.last()
    result = {"final_response": "Completely rewritten", "messages": [], "response_transformed": True}

    conn, _ = _finish(server, state, result, streamed_message=True, streamed_text="Hello")

    assert len(conn.updates) == 1
    _, update = conn.updates[0]
    assert _chunk_text(update) == "Completely rewritten"
    assert update.message_id != streamed_id


def test_noop_transform_sends_nothing():
    server = _make_server()
    state = _streamed_state()
    result = {"final_response": "Hello", "messages": [], "response_transformed": True}

    conn, _ = _finish(server, state, result, streamed_message=True, streamed_text="Hello")

    assert conn.updates == []


def test_unstreamed_transformed_final_still_sends_full_text_on_its_own_id():
    server = _make_server()
    state = _streamed_state()
    result = {"final_response": "Fresh full text", "messages": [], "response_transformed": True}

    conn, _ = _finish(server, state, result, streamed_message=False)

    assert len(conn.updates) == 1
    _, update = conn.updates[0]
    assert _chunk_text(update) == "Fresh full text"
    assert update.message_id == state.message_ids.last()


def test_streamed_untransformed_final_sends_nothing():
    server = _make_server()
    state = _streamed_state()
    result = {"final_response": "Hello", "messages": [], "response_transformed": False}

    conn, _ = _finish(server, state, result, streamed_message=True, streamed_text="Hello")

    assert conn.updates == []
