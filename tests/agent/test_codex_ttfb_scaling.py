"""Tests for Codex TTFB timeout scaling logic in chat_completion_helpers.py."""

import pytest

from agent.chat_completion_helpers import estimate_request_context_tokens


def test_estimate_request_context_tokens():
    """Verify context token estimation handles various payload shapes."""
    # Small payload (< 10k)
    small_messages = [{"role": "user", "content": "hello"}]
    assert estimate_request_context_tokens(small_messages) < 1000

    # Medium payload (10-50k)
    medium_messages = [
        {"role": "user", "content": "x" * 20000},
        {"role": "assistant", "content": "x" * 20000},
    ]
    # chars // 4 approx, so ~10000 tokens.
    assert 10000 <= estimate_request_context_tokens(medium_messages) < 50000

    # Large payload (> 100k)
    large_messages = [
        {"role": "user", "content": "x" * 100000},
        {"role": "assistant", "content": "x" * 100000},
        {"role": "user", "content": "x" * 100000},
    ]
    assert estimate_request_context_tokens(large_messages) >= 100000

    # Responses API shape (dict with "input")
    responses_payload = {
        "input": "x" * 100000,
        "instructions": "System prompt here.",
        "tools": [{"name": "tool", "desc": "x" * 10000}],
    }
    # Verify it counts input + instructions + tools
    assert estimate_request_context_tokens(responses_payload) >= 30000