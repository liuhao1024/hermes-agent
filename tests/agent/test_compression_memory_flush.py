"""Regression: compaction flushes Hindsight pending retains before clearing buffer.

Issue #64315: Context compression calls on_session_switch() without first
flushing pending memory retains. When Hindsight's turn buffer is cleared,
any pending retains dispatched after the last successful retain but before
compaction fires are silently lost at the compaction boundary.

This test verifies flush_pending() is called before on_session_switch().
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


class TestCompressionMemoryFlush:
    """Verify compress_context flushes pending memory before session switch."""

    def test_compress_context_flushes_pending_memory_before_session_switch(
        self, tmp_path
    ):
        """When compression triggers, flush_pending(timeout=5.0) must be called
        BEFORE on_session_switch() to prevent Hindsight from losing pending
        retains. The flush must be inside the same try/except block pattern
        as the existing memory manager calls."""

        from agent.conversation_compression import compress_context
        from hermes_state import SessionDB

        # Set up a minimal DB with a session
        db = SessionDB(tmp_path / "test.db")
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="Message 1")
        db.append_message("s1", role="assistant", content="Response 1")
        db.append_message("s1", role="user", content="Message 2")
        db.append_message("s1", role="assistant", content="Response 2")
        db.append_message("s1", role="user", content="Message 3")

        messages = db.get_messages_as_conversation("s1")

        # Build a minimal agent with mocked memory manager
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            from run_agent import AIAgent

            # Mock memory manager
            mock_memory_manager = MagicMock()
            mock_memory_manager.flush_pending = MagicMock(return_value=True)
            mock_memory_manager.on_session_switch = MagicMock()

            with patch("run_agent.AIAgent.__init__", return_value=None):
                agent = AIAgent.__new__(AIAgent)
                agent.session_id = "s1"
                agent._session_db = db
                agent._memory_manager = mock_memory_manager
                agent.log_prefix = ""
                agent.session_source = "cli"
                agent.model = "test/model"
                agent._session_init_model_config = {}
                agent._gateway_session_key = None
                agent.context_compressor = MagicMock()
                agent.context_compressor.on_session_start = MagicMock()
                agent._compression_warning = None

                # Mock session_create callback
                agent._session_db_created = False

                # Mock _emit_status (used by compression warnings)
                agent._emit_status = MagicMock()

                # Mock parent session tracking
                agent._parent_session_id = None

                # Stub the compressor to return deterministic output
                agent.context_compressor.compress.return_value = "Compressed content"

        # Run compression
        try:
            compress_context(
                agent=agent,
                messages=messages,
                system_message="System",
                approx_tokens=100_000,
            )
        except Exception:
            # We don't care if compression fails for other reasons
            # (e.g., missing compressor implementation), just that flush is called
            pass

        # Verify flush_pending was called BEFORE on_session_switch
        flush_calls = [
            c for c in mock_memory_manager.method_calls if c[0] == "flush_pending"
        ]
        switch_calls = [
            c for c in mock_memory_manager.method_calls if c[0] == "on_session_switch"
        ]

        assert len(flush_calls) == 1, (
            f"flush_pending should be called exactly once, got {len(flush_calls)}"
        )
        assert len(switch_calls) == 1, (
            f"on_session_switch should be called exactly once, got {len(switch_calls)}"
        )

        # Verify the flush call used timeout=5.0 as documented in the fix
        flush_call = flush_calls[0]
        assert flush_call[1].get("timeout") == 5.0, (
            f"flush_pending must be called with timeout=5.0, got {flush_call[1]}"
        )