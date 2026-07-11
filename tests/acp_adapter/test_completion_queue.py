"""Tests for ACP completion queue consumer."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("acp")  # Skip if acp package is not installed

from acp.schema import AgentMessageChunk

# Test the completion queue consumer


@pytest.mark.asyncio
async def test_acp_completion_queue_routes_to_correct_session() -> None:
    """Verify that completion events are routed to the correct ACP session."""
    # Import after pytest to avoid import-time side effects
    from acp_adapter.server import HermesACPAgent
    from acp_adapter.session import SessionManager, SessionState

    # Create mock session manager with two sessions
    session_manager = MagicMock(spec=SessionManager)
    session1 = MagicMock(spec=SessionState)
    session1.session_id = "session-1"
    session1.session_key = "key-1"
    session2 = MagicMock(spec=SessionState)
    session2.session_id = "session-2"
    session2.session_key = "key-2"
    session_manager._sessions = {
        "session-1": session1,
        "session-2": session2,
    }

    # Create agent instance
    agent = HermesACPAgent(session_manager=session_manager)

    # Mock the ACP client connection
    mock_conn = MagicMock(spec=object)
    mock_conn.session_update = MagicMock()

    # Mock process_registry.completion_queue
    mock_queue = MagicMock()
    test_event = {
        "pid": 12345,
        "session_key": "key-1",  # Belongs to session-1
        "type": "completion",
        "exit_code": 0,
    }
    mock_queue.get = MagicMock(side_effect=[test_event, Exception("timeout")])

    with patch(
        "tools.process_registry.process_registry.completion_queue", mock_queue
    ), patch(
        "tools.process_registry.format_process_notification",
        return_value="[Background process 12345 completed]",
    ):
        # Connect the client (starts the background task)
        agent.on_connect(mock_conn)

        # Wait a bit for the task to process the event
        await asyncio.sleep(0.2)

        # Verify that session_update was called for the correct session
        mock_conn.session_update.assert_called_once()
        call_args = mock_conn.session_update.call_args[0][0]
        assert isinstance(call_args, AgentMessageChunk)
        assert call_args.session_id == "session-1"
        assert call_args.content == "[Background process 12345 completed]"

        # Stop the background task
        agent._completion_queue_stop.set()
        if agent._completion_queue_task:
            agent._completion_queue_task.cancel()


@pytest.mark.asyncio
async def test_acp_completion_queue_requeues_foreign_events() -> None:
    """Verify that events for other sessions are re-queued."""
    from acp_adapter.server import HermesACPAgent
    from acp_adapter.session import SessionManager, SessionState

    # Create mock session manager with one session
    session_manager = MagicMock(spec=SessionManager)
    session1 = MagicMock(spec=SessionState)
    session1.session_id = "session-1"
    session1.session_key = "key-1"
    session_manager._sessions = {"session-1": session1}

    # Create agent instance
    agent = HermesACPAgent(session_manager=session_manager)

    # Mock the ACP client connection
    mock_conn = MagicMock(spec=object)
    mock_conn.session_update = MagicMock()

    # Mock process_registry.completion_queue
    mock_queue = MagicMock()
    test_event = {
        "pid": 67890,
        "session_key": "key-2",  # Belongs to a non-existent session
        "type": "completion",
    }
    mock_queue.get = MagicMock(side_effect=[test_event, Exception("timeout")])
    mock_queue.put = MagicMock()

    with patch(
        "tools.process_registry.process_registry.completion_queue", mock_queue
    ):
        # Connect the client (starts the background task)
        agent.on_connect(mock_conn)

        # Wait a bit for the task to process the event
        await asyncio.sleep(0.2)

        # Verify that the event was re-queued (not sent to session_update)
        mock_conn.session_update.assert_not_called()
        mock_queue.put.assert_called_once_with(test_event)

        # Stop the background task
        agent._completion_queue_stop.set()
        if agent._completion_queue_task:
            agent._completion_queue_task.cancel()


@pytest.mark.asyncio
async def test_acp_completion_queue_deduplicates_events() -> None:
    """Verify that the same event is not emitted multiple times."""
    from acp_adapter.server import HermesACPAgent
    from acp_adapter.session import SessionManager, SessionState

    # Create mock session manager
    session_manager = MagicMock(spec=SessionManager)
    session1 = MagicMock(spec=SessionState)
    session1.session_id = "session-1"
    session1.session_key = "key-1"
    session_manager._sessions = {"session-1": session1}

    # Create agent instance
    agent = HermesACPAgent(session_manager=session_manager)

    # Mock the ACP client connection
    mock_conn = MagicMock(spec=object)
    mock_conn.session_update = MagicMock()

    # Mock process_registry.completion_queue
    mock_queue = MagicMock()
    test_event = {
        "pid": 11111,
        "session_key": "key-1",
        "type": "completion",
    }
    # Same event appears twice
    mock_queue.get = MagicMock(
        side_effect=[test_event, test_event, Exception("timeout")]
    )

    with patch(
        "tools.process_registry.process_registry.completion_queue", mock_queue
    ), patch(
        "tools.process_registry.format_process_notification",
        return_value="[Background process 11111 completed]",
    ):
        # Connect the client
        agent.on_connect(mock_conn)

        # Wait for processing
        await asyncio.sleep(0.2)

        # Verify that session_update was called only once (deduplication)
        assert mock_conn.session_update.call_count == 1

        # Stop the background task
        agent._completion_queue_stop.set()
        if agent._completion_queue_task:
            agent._completion_queue_task.cancel()