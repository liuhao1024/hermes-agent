"""Batch trajectory/RL runs (``batch_runner``) resolve flagged commands from
``approvals.batch_mode`` (default deny). Before the marker existed, a batch process
had no CLI/gateway/ask presence and no registered unattended context, so the exec
gate returned ``approved`` without ever consulting ``detect_dangerous_command()`` —
model-emitted shell ran with no prompt, no denial and no human decision record
(#144553)."""

import pytest

import tools.approval as approval_mod
from tools import approval_context

SESSION = "test-batch-context"


@pytest.fixture
def batch_session(monkeypatch):
    monkeypatch.setattr(approval_mod, "_YOLO_MODE_FROZEN", False)
    monkeypatch.setattr(approval_context, "_get_approval_mode", lambda: "manual")
    for var in ("HERMES_CRON_SESSION", "HERMES_GATEWAY_SESSION", "HERMES_INTERACTIVE",
                "HERMES_SINGLE_QUERY_SESSION", "HERMES_SESSION_PLATFORM"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HERMES_APPROVAL_CONTEXT", "batch")
    monkeypatch.setenv("HERMES_SESSION_KEY", SESSION)
    monkeypatch.setattr(approval_mod, "_human_decision",
                        lambda *a, **k: pytest.fail("batch run reached the human approval gate"))
    approval_mod._session_approved.pop(SESSION, None)
    yield
    approval_mod.unregister_gateway_notify(SESSION)


def test_batch_run_denies_flagged_commands_by_default(batch_session):
    result = approval_mod.check_all_command_guards("sudo systemctl restart nginx", "local")
    assert result["approved"] is False
    assert "approvals.batch_mode" in result["message"]


def test_batch_mode_approve_auto_approves(batch_session, monkeypatch):
    monkeypatch.setattr(approval_context, "_get_batch_approval_mode", lambda: "approve")
    result = approval_mod.check_all_command_guards("sudo systemctl restart nginx", "local")
    assert result["approved"] is True


def test_batch_context_beats_an_inherited_cron_marker(batch_session, monkeypatch):
    # A batch process launched from a cron environment keeps the batch wording and
    # cfg_key: the explicit process-level marker resolves first.
    monkeypatch.setenv("HERMES_CRON_SESSION", "1")
    result = approval_mod.check_all_command_guards("sudo systemctl restart nginx", "local")
    assert result["approved"] is False and "approvals.batch_mode" in result["message"]


def test_batch_execute_code_denies_by_default(batch_session):
    result = approval_mod.check_execute_code_guard("import os\nos.system('id')", "local")
    assert result["approved"] is False and "approvals.batch_mode" in result["message"]


def test_batch_context_is_never_a_gateway_or_answerable_context(batch_session, monkeypatch):
    # A batch process inheriting gateway markers must not park on a pending approval
    # nobody can answer; it fails closed through the batch mode instead.
    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    assert approval_context._is_gateway_approval_context() is False
    assert approval_context._no_user_can_answer() is True
