"""A human grant must be as visible to the model as a denial (#142867).

A denial comes back as a BLOCKED message the agent can reason about; an approval used to
return ``message=None`` with no marker on the action gate, so "approved after a card" and
"no card was needed" were indistinguishable. These tests pin both halves of the fix: the
gate result carries ``user_approved`` + an informational ``message`` on every human grant,
and the shared note annotates exactly one tool result (JSON results gain an ``approval``
field instead of a prefix, so ``json.loads`` consumers keep working).
"""
import json

from unittest.mock import patch

import pytest

import tools.approval as approval_module
from tools import approval_context
from tools.approval import (
    check_all_command_guards, clear_session, request_tool_approval,
)
from tools.approval_context import (
    annotate_result_with_human_grant, note_human_grant, set_current_session_key,
    take_human_grant_note,
)


@pytest.fixture
def isolated_session(monkeypatch, tmp_path):
    """Fresh session_key, clean approval state, manual mode, interactive CLI presence."""
    session_key = "test:session:human_grant_trace"
    token = set_current_session_key(session_key)
    monkeypatch.setenv("HERMES_SESSION_KEY", session_key)
    monkeypatch.setenv("HERMES_INTERACTIVE", "1")
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)
    monkeypatch.setattr(approval_context, "_get_approval_mode", lambda: "manual")
    _saved_permanent = approval_module._permanent_approved.copy()
    _saved_session = {k: v.copy() for k, v in approval_module._session_approved.items()}
    approval_module._permanent_approved.clear()
    approval_module._session_approved.clear()
    try:
        yield session_key
    finally:
        approval_module._permanent_approved.update(_saved_permanent)
        approval_module._session_approved.update(_saved_session)
        take_human_grant_note()  # never leak a note into the next test
        try:
            approval_context._approval_session_key.reset(token)
        except Exception:
            pass
        clear_session(session_key)


def _approve_once(command, description, *, allow_permanent=True):
    return "once"


class TestGateResultCarriesTheGrant:
    def test_command_gate_human_grant_has_message_and_marker(self, isolated_session):
        result = check_all_command_guards(
            "rm -rf /tmp/trace-test", "local", approval_callback=_approve_once,
        )
        assert result["approved"] is True
        assert result["user_approved"] is True
        assert result["message"].startswith("Approved by the user (once) after an approval request.")

    def test_action_gate_plugin_escalation_has_message_and_marker(self, isolated_session):
        """The reporter's repro: a plugin pre_tool_call 'approve' directive escalated through
        request_tool_approval used to return a bare {'approved': True, 'message': None}."""
        result = request_tool_approval(
            "browser_exec", "plugin requires approval", rule_key="trace-test",
            approval_callback=_approve_once,
        )
        assert result["approved"] is True
        assert result["user_approved"] is True
        assert result["message"].startswith("Approved by the user (once) after an approval request.")

    def test_scope_wording_follows_the_choice(self, isolated_session):
        def cb(command, description, *, allow_permanent=True):
            return "session"

        result = check_all_command_guards("rm -rf /tmp/trace-session", "local", approval_callback=cb)
        assert result["approved"] is True
        assert "Approved by the user (session) after an approval request." == result["message"]

    def test_no_card_needed_paths_leave_no_trace(self, isolated_session, monkeypatch):
        """yolo / allowlist / auto-approve grants are policy, not a person's answer: they must
        keep returning message=None so 'no card was needed' stays distinguishable."""
        monkeypatch.setattr(approval_module, "_yolo_active", lambda: True)
        result = check_all_command_guards("rm -rf /tmp/trace-yolo", "local", approval_callback=_approve_once)
        assert result["approved"] is True
        assert result.get("user_approved") is not True
        assert result["message"] is None
        assert take_human_grant_note() is None

    def test_grant_sets_exactly_one_consumable_note(self, isolated_session):
        check_all_command_guards("rm -rf /tmp/trace-note", "local", approval_callback=_approve_once)
        note = take_human_grant_note()
        assert note is not None and note.startswith("Approved by the user (once)")
        assert take_human_grant_note() is None  # consume-once


class TestResultAnnotation:
    def test_json_object_result_gains_approval_field(self):
        note_human_grant("Approved by the user (once) after an approval request.")
        raw = json.dumps({"output": "ok", "exit_code": 0})
        annotated = annotate_result_with_human_grant(raw)
        data = json.loads(annotated)  # still machine-parseable
        assert data["approval"].startswith("Approved by the user")
        assert data["output"] == "ok"
        assert data["exit_code"] == 0

    def test_dict_result_gains_approval_field(self):
        note_human_grant("Approved by the user (once) after an approval request.")
        annotated = annotate_result_with_human_grant({"success": True})
        assert annotated["approval"].startswith("Approved by the user")
        assert annotated["success"] is True

    def test_plain_text_result_gets_one_bracketed_line(self):
        note_human_grant("Approved by the user (once) after an approval request.")
        annotated = annotate_result_with_human_grant("plain output")
        assert annotated == "[Approved by the user (once) after an approval request.]\nplain output"

    def test_invalid_json_text_is_prefixed_not_parsed(self):
        note_human_grant("Approved by the user (once) after an approval request.")
        annotated = annotate_result_with_human_grant("{not json")
        assert annotated.startswith("[Approved by the user")

    def test_without_note_is_identity(self):
        assert annotate_result_with_human_grant(json.dumps({"output": "ok"})) == json.dumps({"output": "ok"})
        assert annotate_result_with_human_grant("plain") == "plain"

    def test_non_str_non_dict_shapes_pass_through(self):
        note_human_grant("Approved by the user (once) after an approval request.")
        assert annotate_result_with_human_grant(None) is None
        assert annotate_result_with_human_grant(42) == 42


class TestEndToEndThroughBlockResolution:
    def test_plugin_escalated_approval_reaches_the_tool_result(self, isolated_session, monkeypatch):
        """The reporter's scenario end to end at the dispatch layer: pre_tool_call escalates,
        the person approves on the card, and the model-visible result names the decision."""
        from hermes_cli.plugins import _resolve_block_from_details

        gate = request_tool_approval(
            "browser_exec", "plugin requires approval", rule_key="trace-e2e",
            approval_callback=_approve_once,
        )
        assert gate["approved"] is True
        # Same thread/context the tool result is finalized in:
        annotated = annotate_result_with_human_grant(json.dumps({"output": "did the thing"}))
        assert json.loads(annotated)["approval"].startswith("Approved by the user")
        # and the block resolution stays silent (the tool runs; nothing is blocked)
        monkeypatch.setenv("HERMES_INTERACTIVE", "1")

        class _Details:
            action = "approve"
            message = "plugin requires approval"
            rule_key = "trace-e2e"

        with patch("tools.approval.request_tool_approval", return_value=gate):
            assert _resolve_block_from_details(_Details(), "browser_exec") is None

    def test_denial_still_blocks_and_leaves_no_note(self, isolated_session):
        def deny(command, description, *, allow_permanent=True):
            return "deny"

        result = check_all_command_guards("rm -rf /tmp/trace-deny", "local", approval_callback=deny)
        assert result["approved"] is False
        assert result["message"].startswith("BLOCKED")
        assert take_human_grant_note() is None

    def test_terminal_guards_consume_the_note(self, monkeypatch):
        """The terminal tool already writes its own ``approval`` field for a human grant; it
        must consume the shared note so the generic annotator cannot add a second line."""
        from tools import terminal_tool

        note_human_grant("Approved by the user (once) after an approval request.")
        monkeypatch.setattr(
            terminal_tool, "_check_all_guards",
            lambda *a, **k: {"approved": True, "user_approved": True, "description": "flagged"},
        )
        verdict = terminal_tool._run_approval_guards("rm -rf /tmp/trace-term", "local", {}, force=False)
        assert verdict.approved_run is True
        assert verdict.note and "approved by the user" in verdict.note
        assert take_human_grant_note() is None  # consumed by the terminal's own note path
