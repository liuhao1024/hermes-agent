"""Regression guard: don't send Anthropic ``thinking`` to Kimi's /coding endpoint.

Kimi's ``api.kimi.com/coding`` endpoint speaks the Anthropic Messages protocol
but has its own thinking semantics.  When ``thinking.enabled`` is present in
the request, Kimi validates the message history and requires every prior
assistant tool-call message to carry OpenAI-style ``reasoning_content``.

The Anthropic path never populates that field, and
``convert_messages_to_anthropic`` strips Anthropic thinking blocks on
third-party endpoints — so after one turn with tool calls the next request
fails with HTTP 400::

    thinking is enabled but reasoning_content is missing in assistant
    tool call message at index N

Kimi on the chat_completions route handles ``thinking`` via ``extra_body`` in
``ChatCompletionsTransport`` (#13503).  On the Anthropic route the right
thing to do is drop the parameter entirely and let Kimi drive reasoning
server-side.
"""

from __future__ import annotations

import pytest


class TestKimiCodingSkipsAnthropicThinking:
    """build_anthropic_kwargs must not inject ``thinking`` for Kimi /coding."""

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://api.kimi.com/coding",
            "https://api.kimi.com/coding/v1",
            "https://api.kimi.com/coding/anthropic",
            "https://api.kimi.com/coding/",
        ],
    )
    def test_kimi_coding_endpoint_omits_thinking(self, base_url: str) -> None:
        from agent.anthropic_adapter import build_anthropic_kwargs

        kwargs = build_anthropic_kwargs(
            model="kimi-k2.5",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            max_tokens=4096,
            reasoning_config={"enabled": True, "effort": "medium"},
            base_url=base_url,
        )
        assert "thinking" not in kwargs, (
            "Anthropic thinking must not be sent to Kimi /coding — "
            "endpoint requires reasoning_content on history we don't preserve."
        )
        assert "output_config" not in kwargs

    def test_kimi_coding_with_explicit_disabled_also_omits(self) -> None:
        from agent.anthropic_adapter import build_anthropic_kwargs

        kwargs = build_anthropic_kwargs(
            model="kimi-k2.5",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            max_tokens=4096,
            reasoning_config={"enabled": False},
            base_url="https://api.kimi.com/coding",
        )
        assert "thinking" not in kwargs

    def test_non_kimi_third_party_still_gets_thinking(self) -> None:
        """MiniMax and other third-party Anthropic endpoints must retain thinking."""
        from agent.anthropic_adapter import build_anthropic_kwargs

        kwargs = build_anthropic_kwargs(
            model="MiniMax-M2.7",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            max_tokens=4096,
            reasoning_config={"enabled": True, "effort": "medium"},
            base_url="https://api.minimax.io/anthropic",
        )
        assert "thinking" in kwargs
        assert kwargs["thinking"]["type"] == "enabled"

    def test_native_anthropic_still_gets_thinking(self) -> None:
        from agent.anthropic_adapter import build_anthropic_kwargs

        kwargs = build_anthropic_kwargs(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            max_tokens=4096,
            reasoning_config={"enabled": True, "effort": "medium"},
            base_url=None,
        )
        assert "thinking" in kwargs

    def test_kimi_root_endpoint_unaffected(self) -> None:
        """Official Kimi root endpoint still receives thinking (uses chat_completions transport).

        Only the /coding route is special-cased. Plain api.kimi.com without /coding
        uses the chat_completions transport, which routes through Kimi's own
        thinking implementation. The Anthropic adapter should not interfere with that.

        This test verifies that api.kimi.com (no /coding) still receives
        thinking. Our custom-Kimi detection should only block custom/proxied Kimi
        endpoints, not official Kimi infrastructure.
        """
        from agent.anthropic_adapter import build_anthropic_kwargs

        kwargs = build_anthropic_kwargs(
            model="kimi-k2.5",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            max_tokens=4096,
            reasoning_config={"enabled": True, "effort": "medium"},
            base_url="https://api.kimi.com",
        )
        assert "thinking" in kwargs, (
            "Official Kimi root endpoint must still receive Anthropic thinking"
        )

    def test_custom_kimi_anthropic_endpoint_omits_thinking(self) -> None:
        """Custom Kimi-compatible endpoint with api_mode=anthropic_messages must skip thinking.

        Bug #17057: Users may deploy custom Kimi-compatible endpoints (proxies,
        self-hosted) that implement Anthropic Messages protocol but reject the
        ``thinking`` parameter unless the endpoint is the official Kimi /coding URL.

        Since custom endpoints don't have ``/coding`` in the URL, the existing
        URL-based guard (_is_kimi_coding_endpoint) doesn't catch them. This test
        verifies that our new detection logic catches custom Kimi endpoints by checking
        both model family (kimi-*) and third-party endpoint status.

        Test coverage:
        1. Official Kimi /coding endpoint: still works (already covered by existing tests)
        2. Custom Kimi-compatible endpoint: NEW - must skip thinking
        3. Third-party non-Kimi endpoint: must still receive thinking
        """
        from agent.anthropic_adapter import build_anthropic_kwargs

        # Official Kimi /coding endpoint: already covered by existing test
        # but verify it still works after our changes
        kwargs_official = build_anthropic_kwargs(
            model="kimi-k2.5",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            max_tokens=4096,
            reasoning_config={"enabled": True, "effort": "medium"},
            base_url="https://api.kimi.com/coding",
        )
        assert "thinking" not in kwargs_official, (
            "Official Kimi /coding endpoint must not receive Anthropic thinking"
        )

        # Custom Kimi-compatible endpoint (e.g., proxy or self-hosted)
        # This is the bug #17057: it was receiving thinking and failing
        kwargs_custom = build_anthropic_kwargs(
            model="kimi-k2.5",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            max_tokens=4096,
            reasoning_config={"enabled": True, "effort": "medium"},
            base_url="http://custom-kimi-proxy.com/anthropic",
        )
        assert "thinking" not in kwargs_custom, (
            "Custom Kimi-compatible endpoint must not receive Anthropic thinking"
        )

        # Third-party non-Kimi endpoint should still receive thinking
        kwargs_non_kimi = build_anthropic_kwargs(
            model="mini-max-m2.7",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            max_tokens=4096,
            reasoning_config={"enabled": True, "effort": "medium"},
            base_url="https://api.minimax.io/anthropic",
        )
        assert "thinking" in kwargs_non_kimi
        assert kwargs_non_kimi["thinking"]["type"] == "enabled"
