"""Tests for _append_model_switch_marker role fix (issue #48338).

The model switch marker must NOT use role="system" because strict providers
(vLLM, Qwen) reject system messages that appear mid-conversation. Using
role="user" is safe — the system prompt is prepended to the API message list,
so a user-role marker can appear at any later position, and the gateway's
sanitize/merge pass already coalesces consecutive user messages.
"""

from __future__ import annotations


from tui_gateway.server import _append_model_switch_marker


class TestAppendModelSwitchMarkerRole:
    """Verify the marker uses role='user', not role='system'."""

    def test_marker_uses_user_role(self) -> None:
        """The history entry must be role='user', not role='system'."""
        session: dict = {"session_key": "test-session", "history": []}
        _append_model_switch_marker(session, model="gpt-4o", provider="openai")
        assert len(session["history"]) == 1
        entry = session["history"][0]
        assert entry["role"] == "user", (
            f"Expected role='user' but got role='{entry['role']}'. "
            "Strict providers (vLLM, Qwen) reject mid-conversation system messages."
        )




class TestModelSwitchMarkerDedup:
    """#65891: only the newest marker is meaningful; older ones must not
    accumulate in the live history and burn context tokens every turn."""

    @staticmethod
    def _markers(session: dict) -> list:
        from tui_gateway.server import _is_model_switch_marker

        return [h for h in session["history"] if _is_model_switch_marker(h)]

    def test_second_switch_replaces_first_marker(self) -> None:
        session: dict = {"session_key": "s", "history": []}
        _append_model_switch_marker(session, model="model-a", provider="p")
        _append_model_switch_marker(session, model="model-b", provider="p")
        markers = self._markers(session)
        assert len(markers) == 1, "a second switch must replace, not stack, the marker"
        assert "model-b" in markers[0]["content"]
        assert "model-a" not in markers[0]["content"]
        # The surviving marker is the last history entry.
        assert session["history"][-1] is markers[0]


    def test_dedup_preserves_real_conversation_turns(self) -> None:
        session: dict = {
            "session_key": "s",
            "history": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
        }
        _append_model_switch_marker(session, model="model-a", provider="p")
        _append_model_switch_marker(session, model="model-b", provider="p")
        # Real turns untouched; exactly one marker, appended at the end.
        assert session["history"][0] == {"role": "user", "content": "hello"}
        assert session["history"][1] == {"role": "assistant", "content": "hi"}
        assert len(self._markers(session)) == 1
        assert len(session["history"]) == 3

    def test_prior_marker_between_turns_is_removed(self) -> None:
        # A stale marker not at the tail (a later turn followed it) is still
        # stripped on the next switch.
        session: dict = {
            "session_key": "s",
            "history": [
                {"role": "user", "content": "q1"},
                _make_marker_entry("model-a"),
                {"role": "assistant", "content": "a1"},
            ],
        }
        _append_model_switch_marker(session, model="model-b", provider="p")
        markers = self._markers(session)
        assert len(markers) == 1
        assert "model-b" in markers[0]["content"]
        # The real turns are preserved in order.
        assert [h["content"] for h in session["history"] if not _is_marker(h)] == ["q1", "a1"]

    def test_history_version_increments_once_on_replace(self) -> None:
        session: dict = {"session_key": "s", "history": [], "history_version": 0}
        _append_model_switch_marker(session, model="model-a", provider="p")
        _append_model_switch_marker(session, model="model-b", provider="p")
        assert session["history_version"] == 2  # one increment per switch


def _make_marker_entry(model: str) -> dict:
    from tui_gateway.server import _MODEL_SWITCH_MARKER_PREFIX

    return {"role": "user", "content": f"{_MODEL_SWITCH_MARKER_PREFIX}{model}.]"}


def _is_marker(entry: dict) -> bool:
    from tui_gateway.server import _is_model_switch_marker

    return _is_model_switch_marker(entry)


def _make_welded_entry(prompt: str, model: str = "model-a") -> dict:
    """A marker row after the sanitize pass welded the user prompt into it (#94486 keeps the
    pair addressable but the merged content still opens with the marker paragraph)."""
    from tui_gateway.server import _MODEL_SWITCH_MARKER_PREFIX

    marker = (
        f"{_MODEL_SWITCH_MARKER_PREFIX}{model} via provider p. From this point forward, use this "
        "runtime metadata when answering questions about what model/provider is active.]")
    return {"role": "user", "content": f"{marker}\n\n{prompt}"}


class TestStripKeepsWeldedPrompt:
    """#141240: the stale-marker strip must remove only the marker paragraph, never the user
    prompt a marker row absorbed. Before the fix the whole welded row was dropped, the model-facing
    history started at an assistant turn, and the Anthropic converter padded it with "(empty)"."""

    def test_second_switch_keeps_welded_prompt(self) -> None:
        session: dict = {
            "session_key": "s",
            "history": [
                _make_welded_entry("FIRST PROMPT"),
                {"role": "assistant", "content": "answer"},
            ],
        }
        _append_model_switch_marker(session, model="model-b", provider="p")
        contents = [str(h.get("content")) for h in session["history"]]
        assert any("FIRST PROMPT" in c for c in contents), (
            "a second model switch must not delete the user prompt welded into the stale marker row")
        # The kept prompt is a plain user row in order, ahead of the assistant answer.
        prompt_rows = [h for h in session["history"] if "FIRST PROMPT" in str(h.get("content"))]
        assert prompt_rows[0]["role"] == "user"
        first_assistant = next(
            i for i, h in enumerate(session["history"]) if h.get("role") == "assistant")
        assert session["history"].index(prompt_rows[0]) < first_assistant
        # And the marker paragraph itself is gone from that row.
        assert not _is_marker(prompt_rows[0])
        assert "changed to" not in str(prompt_rows[0]["content"])

    def test_welded_prompt_with_brackets_survives_intact(self) -> None:
        # A prompt carrying "]" and blank lines must not confuse the paragraph boundary.
        session: dict = {
            "session_key": "s",
            "history": [_make_welded_entry("see [fig 1]\n\nthen [refs] end")],
        }
        _append_model_switch_marker(session, model="model-b", provider="p")
        kept = [h for h in session["history"] if h.get("role") == "user" and not _is_marker(h)]
        assert [str(h["content"]) for h in kept] == ["see [fig 1]\n\nthen [refs] end"]

    def test_bare_marker_is_still_dropped(self) -> None:
        session: dict = {
            "session_key": "s",
            "history": [
                _make_marker_entry("model-a"),
                {"role": "user", "content": "hello"},
            ],
        }
        _append_model_switch_marker(session, model="model-b", provider="p")
        assert [h for h in session["history"] if _is_marker(h)] == [
            h for h in session["history"] if "model-b" in str(h.get("content"))]
        assert "hello" in [str(h.get("content")) for h in session["history"]]

    def test_welded_row_loses_its_persisted_stamp(self) -> None:
        # The rewritten row no longer mirrors the persisted bytes; a stale stamp would make
        # the flush scan skip it and the DB would keep the welded content.
        from agent.context_compressor import _DB_PERSISTED_MARKER
        from tui_gateway.server import _strip_stale_model_switch_markers

        welded = _make_welded_entry("FIRST PROMPT")
        welded[_DB_PERSISTED_MARKER] = True
        kept = _strip_stale_model_switch_markers([welded])
        assert len(kept) == 1
        assert _DB_PERSISTED_MARKER not in kept[0]
        assert kept[0]["content"] == "FIRST PROMPT"

    def test_issue_reproduction_no_prompt_loss(self) -> None:
        """End-to-end shape from the report: the sanitize pass welds the prompt into the marker,
        a second model switch strips the stale marker, and the user prompt must survive."""
        import types

        from agent.agent_runtime_helpers import repair_message_sequence
        from tui_gateway.server import _MODEL_SWITCH_MARKER_PREFIX

        marker = {"role": "user", "display_kind": "model_switch",
                  "content": _MODEL_SWITCH_MARKER_PREFIX + "model-a via provider x. From this point "
                              "forward, use this runtime metadata when answering questions about "
                              "what model/provider is active.]"}
        prompt = {"role": "user", "content": "FIRST PROMPT"}
        msgs = [marker, prompt]
        repair_message_sequence(types.SimpleNamespace(), msgs)
        assert len(msgs) == 1  # welded, as reported

        session: dict = {"session_key": "s", "history": list(msgs)}
        _append_model_switch_marker(session, model="model-c", provider="x")
        assert any("FIRST PROMPT" in str(h.get("content")) for h in session["history"])
