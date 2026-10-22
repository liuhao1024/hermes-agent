"""Tests for the MCP elicitation handler in tools.mcp_tool_sampling.

These tests exercise ElicitationHandler in isolation -- the underlying
approval system and the MCP transport layer are mocked, so no real MCP
server or user input is required.

Tests skip cleanly if the optional `mcp` SDK is not installed (it is an
optional dependency under the `[mcp]` extra).
"""

import asyncio
from unittest.mock import patch

import pytest


pytest.importorskip("mcp.types")

from mcp.types import ElicitResult

from tools.mcp_tool_sampling import ElicitationHandler


def _form_params(message="please confirm", schema=None, meta=None):
    """Build a stand-in for ElicitRequestFormParams.

    We use a plain object (not the SDK type directly) so the test doesn't
    couple to optional Pydantic validation -- the handler reads fields via
    getattr() and tolerates duck-typed inputs.
    """
    from types import SimpleNamespace
    return SimpleNamespace(
        mode="form",
        message=message,
        requested_schema=schema or {},
        meta=meta,
    )


def _url_params(message="open this url", url="https://example.com/auth", elicitation_id="e1"):
    from types import SimpleNamespace
    return SimpleNamespace(
        mode="url",
        message=message,
        url=url,
        elicitation_id=elicitation_id,
    )




class TestElicitationHandlerFormMode:
    def test_user_accepts_once_returns_accept(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _form_params(
            "authorize a payment of $0.50",
            {"properties": {"approved": {"type": "boolean"}}},
        )

        with patch("tools.approval_prompt.request_elicitation_consent", return_value="accept"):
            result = asyncio.run(handler(context=None, params=params))

        assert isinstance(result, ElicitResult)
        assert result.action == "accept"
        assert result.content == {}
        assert handler.metrics["accepted"] == 1
        assert handler.metrics["declined"] == 0



    def test_cancel_propagates_through(self):
        """request_elicitation_consent returns 'cancel' when the gateway
        wait times out (resolved=False). The handler should propagate
        that as ElicitResult(action='cancel') so the server can
        distinguish 'no answer' from 'no'."""
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _form_params()

        with patch("tools.approval_prompt.request_elicitation_consent", return_value="cancel"):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "cancel"
        assert handler.metrics["errors"] == 1


class TestElicitationHandlerFailureModes:
    def test_url_mode_is_declined_without_prompting(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _url_params()

        # If the handler tried to prompt, this would raise AssertionError
        # because the side_effect treats the call as a test failure.
        with patch(
            "tools.approval_prompt.request_elicitation_consent",
            side_effect=AssertionError("URL mode must not prompt"),
        ):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "decline"
        assert handler.metrics["declined"] == 1

    def test_exception_in_approval_fails_closed_to_decline(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _form_params()

        with patch(
            "tools.approval_prompt.request_elicitation_consent",
            side_effect=RuntimeError("approval system blew up"),
        ):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "decline"
        assert handler.metrics["errors"] == 1

    def test_timeout_returns_cancel(self, monkeypatch):
        # Shrink the outer grace window so the test budget is just the
        # handler timeout. Default grace is 5s, which makes stall durations
        # tight and the test flaky.
        monkeypatch.setattr(
            ElicitationHandler, "_OUTER_TIMEOUT_GRACE_SECONDS", 0
        )
        # _safe_numeric clamps `timeout` to a minimum of 1s, so the
        # effective wait_for budget is 1s here. Stall longer than that
        # so the wait_for reliably fires TimeoutError.
        handler = ElicitationHandler("pay", {"timeout": 0.05})
        params = _form_params()

        def stall(*_args, **_kwargs):
            import time as _t
            _t.sleep(2)
            return "accept"

        with patch("tools.approval_prompt.request_elicitation_consent", side_effect=stall):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "cancel"
        assert handler.metrics["errors"] == 1




class TestElicitationHandlerContextBridge:
    """The MCP recv-loop task that fires elicitation callbacks does NOT
    inherit the agent's contextvars (HERMES_SESSION_PLATFORM etc.). The
    handler reads the ``call_context`` thunk's snapshot -- a snapshot captured
    by the MCP tool wrapper around ``session.call_tool`` -- and replays
    it before invoking the approval router so gateway-session detection
    survives the task hop. Regression tests for that bridge."""

    def test_captured_context_is_replayed_in_consent_call(self):
        """The captured context's contextvar values must be observable
        when ``request_elicitation_consent`` runs -- otherwise the
        gateway-platform detection in approval.py sees an empty platform
        string and falls back to the CLI path (the bug this fixes)."""
        import contextvars

        probe: contextvars.ContextVar[str] = contextvars.ContextVar(
            "elicitation_test_probe", default=""
        )
        seen: list[str] = []

        def fake_consent(*_args, **_kwargs):
            seen.append(probe.get())
            return "accept"

        token = probe.set("gateway:telegram")
        try:
            captured = contextvars.copy_context()
        finally:
            probe.reset(token)
        assert probe.get() == "", (
            "Sanity check: the probe must be empty outside the captured "
            "context, otherwise the test would pass even without replay."
        )

        handler = ElicitationHandler("pay", {"timeout": 5}, call_context=lambda: captured)
        params = _form_params()

        with patch("tools.approval_prompt.request_elicitation_consent", side_effect=fake_consent):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "accept"
        assert seen == ["gateway:telegram"], (
            f"Expected the captured contextvar to be visible inside the "
            f"consent call; got {seen!r}"
        )

    def test_missing_captured_context_falls_back_to_direct_call(self):
        """With the default call_context (or one whose task has not entered a tool
        call) the handler must still invoke the consent router -- just
        without the contextvar replay. Otherwise CLI/TUI sessions, which
        don't set HERMES_SESSION_PLATFORM, would break."""
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _form_params()

        with patch("tools.approval_prompt.request_elicitation_consent", return_value="accept") as m:
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "accept"
        assert m.call_count == 1


    def test_pending_call_context_none_does_not_crash(self):
        """The ``call_context`` thunk returns None between tool
        calls. An elicitation arriving in that window must not crash."""
        handler = ElicitationHandler("pay", {"timeout": 5}, call_context=lambda: None)
        params = _form_params()

        with patch("tools.approval_prompt.request_elicitation_consent", return_value="decline"):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "decline"


class TestRequestedSchemaFieldName:
    """The requested schema must be read off the *real* SDK model.

    Every other test in this file builds a duck-typed ``SimpleNamespace``
    stand-in for the params object. That keeps them cheap, but it means none
    of them can catch the handler reading a field name the SDK model does not
    actually have -- the stand-in simply has whatever name the test wrote.

    The SDK spells this field ``requestedSchema`` on mcp 1.x and
    ``requested_schema`` on 2.0 (which renamed model fields to snake_case and
    kept camelCase only as a serialization alias, which pydantic does not
    expose to attribute access). Constructing with the camelCase spelling
    works on both -- 2.0 accepts it as the alias -- so this test pins the
    behaviour to the real model on whichever SDK is installed.
    """

    def test_real_sdk_params_schema_reaches_the_consent_description(self):
        from mcp.types import ElicitRequestFormParams

        params = ElicitRequestFormParams(
            message="authorize a payment of $0.50",
            requestedSchema={
                "type": "object",
                "properties": {
                    "card_number": {
                        "type": "string",
                        "description": "card to charge",
                    },
                },
            },
        )
        handler = ElicitationHandler("pay", {"timeout": 5})
        captured: dict = {}

        def _capture(*args, **kwargs):
            captured["description"] = kwargs.get("description") or (
                args[1] if len(args) > 1 else ""
            )
            return "decline"

        with patch("tools.approval_prompt.request_elicitation_consent", _capture):
            asyncio.run(handler(context=None, params=params))

        # An empty schema renders the generic "Approval requested by ..."
        # fallback, so the field name is what proves the schema was read.
        assert "card_number" in (captured.get("description") or ""), captured


def _remember_key(server: str, message: str) -> str:
    """The handler's approval key for a remembered elicitation choice."""
    import hashlib
    digest = hashlib.sha256(message.encode("utf-8", "replace")).hexdigest()[:16]
    return f"mcp_elicitation/{server}/{digest}"


class TestConsentRemembersSessionChoice:
    """Issue #142803: ``request_elicitation_consent`` keeps a ``session``/``always`` answer
    under the caller's remember_key, and the next identical request short-circuits to
    ``accept`` without re-prompting. A ``once`` answer persists nothing."""

    def test_once_persists_nothing_then_session_persists_and_short_circuits(self, monkeypatch):
        """Full consent-router round trip on the api_server gateway path: a client resolving
        with ``once`` stores nothing; resolving with ``session`` stores the key, and the
        very next identical consent returns ``accept`` with no second prompt."""
        from gateway.session_context import clear_session_vars, set_session_vars
        from tools import approval, approval_prompt
        from tools.approval_context import (
            reset_current_session_key,
            set_current_session_key,
        )

        session_key = "elicitation-remember"
        seen = []
        # Fail fast instead of waiting on the CLI input timeout if routing goes wrong.
        monkeypatch.setattr(approval_prompt, "prompt_dangerous_approval",
                            lambda *_args, **_kwargs: "deny")

        def notify(approval_data):
            seen.append(dict(approval_data))
            assert approval.resolve_gateway_approval(
                session_key, notify.choice, request_id=approval_data["request_id"]) == 1

        session_tokens = set_session_vars(
            platform="api_server", session_key=session_key, async_delivery=False
        )
        key_token = set_current_session_key(session_key)
        approval.register_gateway_notify(session_key, notify)
        key = _remember_key("codex", "Allow Computer Use?")
        try:
            notify.choice = "once"
            assert approval_prompt.request_elicitation_consent(
                "Allow Computer Use?", "d", remember_key=key) == "accept"
            assert not approval.is_approved(session_key, key), "once must persist nothing"

            notify.choice = "session"
            assert approval_prompt.request_elicitation_consent(
                "Allow Computer Use?", "d", remember_key=key) == "accept"
            assert approval.is_approved(session_key, key), "session must persist the key"

            # Stored choice: answered without a third prompt.
            assert approval_prompt.request_elicitation_consent(
                "Allow Computer Use?", "d", remember_key=key) == "accept"
        finally:
            approval.unregister_gateway_notify(session_key)
            reset_current_session_key(key_token)
            clear_session_vars(session_tokens)
            approval.clear_session(session_key)

        assert len(seen) == 2, f"expected exactly two prompts, got {len(seen)}"
        assert seen[0].get("allow_session") is not False, (
            "a rememberable consent must keep offering the session scope"
        )


class TestPersistScopeEcho:
    """Issue #142803: an ``accept`` covered by a stored scope echoes that scope back in
    ``_meta.persist`` -- but only in the vocabulary the server advertised."""

    def _run(self, message, meta, stored_scope, handler_name="codex"):
        from tools import approval
        from tools.approval_context import (
            reset_current_session_key,
            set_current_session_key,
        )
        handler = ElicitationHandler(handler_name, {"timeout": 5})
        params = _form_params(message, meta=meta)
        key = _remember_key(handler_name, message)
        session_key = "elicitation-scope-echo"
        token = set_current_session_key(session_key)
        try:
            if stored_scope == "session":
                approval.approve_session(session_key, key)
            elif stored_scope == "always":
                approval.approve_permanent(key)
            with patch("tools.approval_prompt.request_elicitation_consent", return_value="accept"):
                return asyncio.run(handler(context=None, params=params))
        finally:
            reset_current_session_key(token)
            approval.clear_session(session_key)
            approval._permanent_set().discard(key)

    def test_session_scope_echoed_when_server_offers_both_modes(self):
        result = self._run("Allow Computer Use to use 'Google Chrome'?",
                          {"persist": ["session", "always"]}, "session")
        assert result.action == "accept"
        assert result.meta == {"persist": "session"}

    def test_permanent_scope_downgrades_for_a_session_only_server(self):
        result = self._run("Allow Computer Use?", {"persist": ["session"]}, "always")
        assert result.meta == {"persist": "session"}

    def test_scope_not_echoed_when_server_never_asked_for_modes(self):
        result = self._run("Allow Computer Use?", meta=None, stored_scope="session")
        assert result.action == "accept"
        assert result.meta is None

    def test_one_shot_accept_carries_no_meta(self):
        result = self._run("Allow Computer Use?", {"persist": ["session", "always"]},
                           stored_scope=None)
        assert result.action == "accept"
        assert result.meta is None
