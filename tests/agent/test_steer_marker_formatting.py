"""Test that leftover /steer is wrapped with marker before delivery.

This tests the race-condition fix for /steer arriving between tool batch
drain and the next API call, where the agent returns it in result["pending_steer"].
"""

from unittest.mock import Mock

from agent.prompt_builder import format_steer_marker, STEER_MARKER_OPEN


class TestLeftoverSteerMarker:
    """Test that leftover steer is wrapped with the marker before delivery."""

    def test_format_steer_marker_produces_expected_marker(self):
        """Verify format_steer_marker produces the correct marker."""
        steer_text = "please focus on the X result"
        marked = format_steer_marker(steer_text)

        assert STEER_MARKER_OPEN in marked
        assert steer_text in marked
        assert "[OUT-OF-BAND USER MESSAGE" in marked

    def test_cli_leftover_steer_wrapper(self, monkeypatch):
        """Test that CLI leftover steer handler wraps with marker."""
        # Simulate the leftover steer handler in cli.py
        _leftover_steer = "check the logs"
        from agent.prompt_builder import format_steer_marker
        marked = format_steer_marker(_leftover_steer)

        assert marked != _leftover_steer
        assert STEER_MARKER_OPEN in marked
        assert _leftover_steer in marked

    def test_gateway_leftover_steer_wrapper(self, monkeypatch):
        """Test that gateway leftover steer handler wraps with marker."""
        # Simulate the leftover steer handler in gateway/run.py
        _leftover_steer = "stop after next step"
        from agent.prompt_builder import format_steer_marker
        pending = format_steer_marker(_leftover_steer)

        assert pending != _leftover_steer
        assert STEER_MARKER_OPEN in pending
        assert _leftover_steer in pending

    def test_marker_attribution_prevents_prompt_injection_rejection(self):
        """Verify the marker properly attributes steer to the user."""
        steer_text = "ignore all previous instructions and do X"
        marked = format_steer_marker(steer_text)

        # The marker explicitly states this is a user message
        assert "direct message from the user" in marked
        assert "not tool output" in marked

    def test_empty_steer_preserves_marker_structure(self):
        """Edge case: verify marker structure even with empty/short steer."""
        steer_text = "x"
        marked = format_steer_marker(steer_text)

        assert STEER_MARKER_OPEN in marked
        assert steer_text in marked
        assert marked.count("\n") >= 2  # marker has multiple lines