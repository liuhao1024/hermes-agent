"""Tests for the post-acquire trust-gate recheck (#144380).

Security boundary under test: the call-time gate classifies from the cached
``readOnlyHint`` before any transport work. A lazy or reconnecting server's
rediscovery runs inside ``_acquire_call_server`` and can flip that hint from
read-only to write-capable after the gate already cleared the call — the
handler must re-decide consent against the session that runs the call before
dispatching, and a denied re-ask must refund the spend the gate reserved.

Adversarial notes encoded in these tests:
- The flip direction that matters is read-only -> write-capable: consent
  decided on stale "safe" metadata must not survive fresh "unsafe" metadata.
- An explicit prior approval (session/always grant) keeps covering the tool
  after the flip; the recheck never asks twice for one call.
- A denied re-ask must leave no reserved spend behind.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from tools import mcp_tool
from tools import mcp_tool_handlers as _mcp_handlers
from tools.mcp_tool_costs import CostPolicy, ToolCost


@pytest.fixture(autouse=True)
def _clean_trust_state():
    """Isolate the module-level trust metadata and spend tally between tests."""
    with patch.dict(mcp_tool._server_trust_levels, {}, clear=True), \
         patch.dict(mcp_tool._tool_read_only_hints, {}, clear=True), \
         patch.dict(mcp_tool._server_cost_policies, {}, clear=True), \
         patch.dict("tools.mcp_tool_costs._spent", {}, clear=True):
        yield


def _wire_handler(monkeypatch, *, flip_hint: bool, dispatch_calls_to: MagicMock):
    """Build the srv/edit handler with acquire mocked to run the rediscovery step."""
    mcp_tool._server_trust_levels["srv"] = "untrusted"
    mcp_tool._tool_read_only_hints.setdefault("srv", {})["edit"] = True
    monkeypatch.setattr(_mcp_handlers, "_check_circuit_breaker", lambda name: None)

    def _acquire(name, timeout):
        if flip_hint:  # rediscovery inside acquire flips the cached hint
            mcp_tool._tool_read_only_hints[name]["edit"] = False
        return MagicMock(), None

    monkeypatch.setattr(_mcp_handlers, "_acquire_call_server", _acquire)
    monkeypatch.setattr(_mcp_handlers, "_dispatch", dispatch_calls_to)
    return _mcp_handlers._make_tool_handler("srv", "edit", 30.0)


class TestPostAcquireTrustRecheck:
    def test_flip_to_write_capable_reasks_then_dispatches_on_accept(self, monkeypatch):
        """Gate cleared on the cached read-only hint; the acquire-time flip asks once; accept runs."""
        dispatched = MagicMock(return_value=json.dumps({"result": "ok"}))
        handler = _wire_handler(monkeypatch, flip_hint=True, dispatch_calls_to=dispatched)
        with patch("tools.approval_prompt.request_elicitation_consent",
                   return_value="accept") as consent:
            raw = handler({"path": "x"})
        consent.assert_called_once()  # the gate itself asked nothing; only the recheck did
        assert json.loads(raw) == {"result": "ok"}
        dispatched.assert_called_once()

    def test_flip_denied_reask_blocks_dispatch(self, monkeypatch):
        """A denied re-ask returns the not-run error and the RPC never fires."""
        dispatched = MagicMock(return_value=json.dumps({"result": "ok"}))
        handler = _wire_handler(monkeypatch, flip_hint=True, dispatch_calls_to=dispatched)
        with patch("tools.approval_prompt.request_elicitation_consent", return_value="cancel"):
            raw = handler({"path": "x"})
        assert "NOT run" in raw
        dispatched.assert_not_called()

    def test_denied_reask_refunds_the_gate_reserved_spend(self, monkeypatch):
        """The gate reserved the declared cost on the read-only path; the deny leaves nothing booked."""
        from tools.mcp_tool_costs import _spent
        mcp_tool._server_cost_policies["srv"] = CostPolicy({"edit": ToolCost(3, 3)}, 0, "credits")
        dispatched = MagicMock(return_value=json.dumps({"result": "ok"}))
        handler = _wire_handler(monkeypatch, flip_hint=True, dispatch_calls_to=dispatched)
        with patch("tools.approval_prompt.request_elicitation_consent", return_value="cancel"):
            handler({"path": "x"})
        assert all(v == 0.0 for v in _spent.values()), f"spend not refunded: {_spent}"
        dispatched.assert_not_called()

    def test_explicit_grant_covers_the_flipped_tool_without_reasking(self, monkeypatch):
        """A remembered session/always approval keeps covering the tool after the flip."""
        dispatched = MagicMock(return_value=json.dumps({"result": "ok"}))
        handler = _wire_handler(monkeypatch, flip_hint=True, dispatch_calls_to=dispatched)
        with patch("tools.approval.is_approved", return_value=True), \
             patch("tools.approval_prompt.request_elicitation_consent") as consent:
            raw = handler({"path": "x"})
        consent.assert_not_called()
        assert json.loads(raw) == {"result": "ok"}

    def test_stable_read_only_hint_never_asks(self, monkeypatch):
        """No flip, no prompt: the pre-existing read-only fast path is unchanged."""
        dispatched = MagicMock(return_value=json.dumps({"result": "ok"}))
        handler = _wire_handler(monkeypatch, flip_hint=False, dispatch_calls_to=dispatched)
        with patch("tools.approval_prompt.request_elicitation_consent") as consent:
            raw = handler({"path": "x"})
        consent.assert_not_called()
        assert json.loads(raw) == {"result": "ok"}

    def test_gate_asked_call_is_not_asked_again_after_flip(self, monkeypatch):
        """The gate cleared a write-capable call with one prompt; a later flip to read-only must not ask twice."""
        dispatched = MagicMock(return_value=json.dumps({"result": "ok"}))
        handler = _wire_handler(monkeypatch, flip_hint=True, dispatch_calls_to=dispatched)
        mcp_tool._tool_read_only_hints["srv"]["edit"] = False  # write-capable at gate time
        with patch("tools.approval_prompt.request_elicitation_consent",
                   return_value="accept") as consent:
            raw = handler({"path": "x"})
        consent.assert_called_once()  # the gate's ask; the recheck stands down on hint_before=False
        assert json.loads(raw) == {"result": "ok"}
