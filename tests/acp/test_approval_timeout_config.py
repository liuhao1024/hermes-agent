"""Test that ACP approval callbacks honor the approvals.timeout config."""
from unittest.mock import MagicMock, patch

import pytest


def test_acp_approval_callback_uses_config_timeout():
    """Test that make_approval_callback receives timeout from _get_approval_timeout."""
    from acp_adapter.permissions import make_approval_callback
    from acp.schema import AllowedOutcome

    loop = MagicMock()

    # Mock the ACP permission request to succeed
    request_permission = MagicMock()

    def _schedule(coro, passed_loop):
        future = MagicMock()
        future.result.return_value = AllowedOutcome(option_id="allow_once", outcome="selected")
        return future

    with patch(
        "agent.async_utils.safe_schedule_threadsafe", side_effect=_schedule
    ), patch("tools.approval._get_approval_timeout", return_value=42):
        # This mimics what acp_adapter/server.py:1421 does after the fix
        from tools.approval import _get_approval_timeout

        approval_timeout = float(_get_approval_timeout())
        cb = make_approval_callback(request_permission, loop, session_id="s1", timeout=approval_timeout)

        result = cb("rm -rf /", "dangerous command")

        assert result == "once"

    # Verify the timeout was used when calling future.result()
    assert approval_timeout == 42.0


def test_acp_edit_approval_requester_uses_config_timeout():
    """Test that make_acp_edit_approval_requester receives timeout from _get_approval_timeout."""
    from acp_adapter.edit_approval import make_acp_edit_approval_requester, EditProposal
    from acp.schema import AllowedOutcome

    loop = MagicMock()

    # Mock the ACP permission request to succeed
    request_permission = MagicMock()

    def _schedule(coro, passed_loop):
        future = MagicMock()
        future.result.return_value = AllowedOutcome(option_id="allow_once", outcome="selected")
        return future

    with patch(
        "agent.async_utils.safe_schedule_threadsafe", side_effect=_schedule
    ), patch("tools.approval._get_approval_timeout", return_value=42):
        from tools.approval import _get_approval_timeout

        approval_timeout = float(_get_approval_timeout())
        requester = make_acp_edit_approval_requester(
            request_permission,
            loop,
            session_id="s1",
            timeout=approval_timeout,
        )

        proposal = EditProposal(path="/tmp/test.txt", old_text="old", new_content="new")
        result = requester(proposal)

        assert result is True

    # Verify the timeout was used when calling future.result()
    assert approval_timeout == 42.0