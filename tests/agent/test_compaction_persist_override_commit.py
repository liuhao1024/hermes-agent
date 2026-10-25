"""The current turn's user row keeps its clean transcript form across a compaction commit (#144581).

Mid-turn compaction materializes compressor COPIES as the new active rows while the live user
dict still holds the wire form (injected context blocks). The persist override that maps it back
to the clean transcript form is applied by the normal flush to the DB row only, and the turn
finalizer runs after the commit — so without the fix the committed copy becomes the only active
row and every client that reloads the session renders the wire form as user text (an image turn
shows ``[The user attached an image: ...]`` instead of the ``@image:`` row).
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_WIRE_FORM = (
    "[The user attached an image: upload_x.jpg]\n"
    "[Examine it with the vision_analyze tool using image_url: /Users/me/.hermes/images/upload_x.jpg]\n"
    "\n"
    "Again im unable to download."
)
_TRANSCRIPT_FORM = (
    "Again im unable to download.\n@image:/Users/me/.hermes/images/upload_x.jpg"
)
_SUMMARY_ROW = "[CONTEXT COMPACTION] summary of prior turns"
_TURN_TS = 1761000000.0
_FAKE_KEY = "test-key"


def _make_agent(session_db, session_id, *, in_place, compress_output):
    with patch.dict("os.environ", {"OPENROUTER_API_KEY": _FAKE_KEY}):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key=_FAKE_KEY,
            base_url="https://openrouter.ai/api/v1",
            model="test/model",
            quiet_mode=True,
            session_db=session_db,
            session_id=session_id,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.compression_in_place = in_place

    def _fake_compress(messages, **kwargs):
        # compress() hands back copies of the tail it kept, exactly like a real engine.
        return [dict(m) for m in compress_output]

    agent.context_compressor.compress = _fake_compress
    agent.context_compressor._last_compress_aborted = False
    agent.context_compressor._last_summary_error = None
    agent.context_compressor.compression_count = 1
    return agent


def _seed_session(db, sid):
    db.create_session(sid, "cli", model="test/model")
    # The turn-start flush already wrote the current turn's user row in the clean
    # transcript form; the live list keeps the wire form for the in-flight request.
    db.append_message(session_id=sid, role="user", content="earlier question")
    db.append_message(session_id=sid, role="assistant", content="earlier answer")
    db.append_message(session_id=sid, role="user", content=_TRANSCRIPT_FORM)


def _active_user_rows(db, sid):
    sql = "SELECT content, api_content FROM messages WHERE session_id = ? AND role = 'user' AND active = 1 ORDER BY id"
    return [dict(row) for row in db._conn.execute(sql, (sid,)).fetchall()]


def _mid_turn_messages():
    history = [
        {"role": "user", "content": "earlier question", "timestamp": _TURN_TS - 20},
        {"role": "assistant", "content": "earlier answer", "timestamp": _TURN_TS - 10},
    ]
    turn_user = {"role": "user", "content": _WIRE_FORM, "timestamp": _TURN_TS}
    return history, turn_user


def test_in_place_commit_keeps_transcript_form():
    """In-place commit materializes the transcript form, never the wire form (#144581)."""
    from hermes_state import SessionDB
    from agent.conversation_compression import compress_context

    history, turn_user = _mid_turn_messages()
    with tempfile.TemporaryDirectory() as tmp:
        db = SessionDB(db_path=Path(tmp) / "t.db")
        sid = "20261026_120000_aaaaaa"
        _seed_session(db, sid)
        messages = history + [turn_user]
        compress_output = [{"role": "user", "content": _SUMMARY_ROW}, turn_user]
        agent = _make_agent(db, sid, in_place=True, compress_output=compress_output)
        agent._persist_user_message_idx = len(messages) - 1
        agent._persist_user_message_override = _TRANSCRIPT_FORM
        agent._last_flushed_db_idx = len(db.get_messages(sid))

        compressed, _sp = compress_context(
            agent, messages, approx_tokens=100_000, system_message="sys"
        )

        assert any(m.get("content") == _SUMMARY_ROW for m in compressed)
        rows = _active_user_rows(db, sid)
        contents = [row["content"] for row in rows]
        assert _SUMMARY_ROW in contents
        # The current turn's user row survives compaction in its persisted form.
        assert _TRANSCRIPT_FORM in contents
        assert not any(
            isinstance(c, str) and c.startswith("[The user attached an image:")
            for c in contents
        )
        # The wire bytes ride along as the api_content sidecar for replay parity.
        turn_rows = [row for row in rows if row["content"] == _TRANSCRIPT_FORM]
        assert turn_rows and turn_rows[0]["api_content"] == _WIRE_FORM


def test_rotation_commit_keeps_transcript_form():
    """The rotation child handoff carries the transcript form too (#144581)."""
    from hermes_state import SessionDB
    from agent.conversation_compression import compress_context

    history, turn_user = _mid_turn_messages()
    with tempfile.TemporaryDirectory() as tmp:
        db = SessionDB(db_path=Path(tmp) / "t.db")
        sid = "20261026_120000_bbbbbb"
        _seed_session(db, sid)
        messages = history + [turn_user]
        compress_output = [{"role": "user", "content": _SUMMARY_ROW}, turn_user]
        agent = _make_agent(db, sid, in_place=False, compress_output=compress_output)
        agent._persist_user_message_idx = len(messages) - 1
        agent._persist_user_message_override = _TRANSCRIPT_FORM
        agent._last_flushed_db_idx = len(db.get_messages(sid))

        with patch(
            "agent.conversation_compression._rebind_session_context", return_value=None
        ):
            _compressed, _sp = compress_context(
                agent, messages, approx_tokens=100_000, system_message="sys"
            )

        child_sid = agent.session_id
        assert child_sid != sid  # rotation forked a child
        rows = _active_user_rows(db, child_sid)
        contents = [row["content"] for row in rows]
        assert _SUMMARY_ROW in contents
        assert _TRANSCRIPT_FORM in contents
        assert not any(
            isinstance(c, str) and c.startswith("[The user attached an image:")
            for c in contents
        )


class TestPersistOverrideMatchRules:
    def _fake_agent(self, idx, override):
        return SimpleNamespace(
            _persist_user_message_idx=idx,
            _persist_user_message_override=override,
            _persist_user_message_timestamp=None,
        )

    def test_matched_row_is_rewritten(self):
        from agent.conversation_compression_persist import (
            _apply_persist_override_to_commit_rows,
        )

        live = {"role": "user", "content": _WIRE_FORM, "timestamp": _TURN_TS}
        copy = dict(live)
        _apply_persist_override_to_commit_rows(
            self._fake_agent(0, _TRANSCRIPT_FORM), [live], [copy]
        )
        assert copy["content"] == _TRANSCRIPT_FORM
        assert copy["api_content"] == _WIRE_FORM

    def test_unmatched_timestamp_is_untouched(self):
        from agent.conversation_compression_persist import (
            _apply_persist_override_to_commit_rows,
        )

        live = {"role": "user", "content": _WIRE_FORM, "timestamp": _TURN_TS}
        drifted = {"role": "user", "content": _WIRE_FORM, "timestamp": _TURN_TS + 5}
        _apply_persist_override_to_commit_rows(
            self._fake_agent(0, _TRANSCRIPT_FORM), [live], [drifted]
        )
        assert drifted["content"] == _WIRE_FORM

    def test_merged_row_is_untouched(self):
        """A summary-merged user row no longer equals the live content: never rewritten."""
        from agent.conversation_compression_persist import (
            _apply_persist_override_to_commit_rows,
        )

        live = {"role": "user", "content": _WIRE_FORM, "timestamp": _TURN_TS}
        merged = {
            "role": "user",
            "content": f"summary\n{_WIRE_FORM}",
            "timestamp": _TURN_TS,
        }
        _apply_persist_override_to_commit_rows(
            self._fake_agent(0, _TRANSCRIPT_FORM), [live], [merged]
        )
        assert merged["content"] == f"summary\n{_WIRE_FORM}"

    def test_missing_override_is_untouched(self):
        from agent.conversation_compression_persist import (
            _apply_persist_override_to_commit_rows,
        )

        live = {"role": "user", "content": _WIRE_FORM, "timestamp": _TURN_TS}
        copy = dict(live)
        _apply_persist_override_to_commit_rows(
            self._fake_agent(0, None), [live], [copy]
        )
        assert copy["content"] == _WIRE_FORM
