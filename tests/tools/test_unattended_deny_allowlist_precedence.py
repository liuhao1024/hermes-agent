"""An interactive "Always Allow" click must not exempt commands from an unattended deny mode.

``approvals.cron_mode: deny`` (and the single-query / unattended-platform siblings) is the
operator's "no dangerous command without a human in the loop" contract. The permanent
allowlist records consent granted interactively ("this command, now, with me watching"), so
the unattended deny resolves BEFORE the allowlist and ignores it (#144434). Where a human is
present, the allowlist keeps its veto.
"""

import pytest

import tools.approval as approval_module
from gateway.session_context import clear_session_vars, reset_session_vars, set_session_vars
from tools import approval_context
from tools.approval import check_all_command_guards, detect_dangerous_command

COMMAND = "rm -rf /tmp/stuff"
SESSION = "test-deny-allowlist-precedence"


@pytest.fixture(autouse=True)
def _clear_approval_state(monkeypatch):
    for var in ("HERMES_CRON_SESSION", "HERMES_GATEWAY_SESSION", "HERMES_INTERACTIVE",
                "HERMES_EXEC_ASK", "HERMES_YOLO_MODE", "HERMES_SESSION_PLATFORM"):
        monkeypatch.delenv(var, raising=False)
    approval_module._permanent_approved.clear()
    approval_module._session_approved.pop(SESSION, None)
    reset_session_vars()
    yield
    approval_module._permanent_approved.clear()
    approval_module._session_approved.pop(SESSION, None)
    reset_session_vars()


def _guard_env(monkeypatch):
    """Headless run: no yolo, no approvals off, no human on any bridge."""
    monkeypatch.setattr(approval_module, "_YOLO_MODE_FROZEN", False)
    monkeypatch.setattr(approval_context, "_get_approval_mode", lambda: "manual")
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)


def _always_allow_command():
    """Simulate an earlier interactive Always Allow click for the dangerous command."""
    _, pattern_key, _ = detect_dangerous_command(COMMAND)
    approval_module.approve_permanent(pattern_key)
    return pattern_key


class TestUnattendedDenyIgnoresPermanentAllowlist:
    def test_cron_deny_blocks_allowlisted_dangerous_command(self, monkeypatch):
        _guard_env(monkeypatch)
        _always_allow_command()
        monkeypatch.setenv("HERMES_CRON_SESSION", "1")
        monkeypatch.setattr(approval_context, "_get_cron_approval_mode", lambda: "deny")

        result = check_all_command_guards(COMMAND, "local")

        assert result["approved"] is False
        assert "cron" in result["message"].lower()

    def test_single_query_deny_blocks_allowlisted_dangerous_command(self, monkeypatch):
        _guard_env(monkeypatch)
        _always_allow_command()
        monkeypatch.setattr(approval_module, "_is_single_query_approval_context", lambda: True)
        monkeypatch.setattr(approval_context, "_get_single_query_approval_mode", lambda: "deny")

        result = check_all_command_guards(COMMAND, "local")

        assert result["approved"] is False
        assert result["pattern_key"], "single-query blocks carry the pattern_key detail"

    def test_unattended_platform_deny_blocks_allowlisted_dangerous_command(self, monkeypatch):
        _guard_env(monkeypatch)
        _always_allow_command()
        monkeypatch.setenv("HERMES_SESSION_PLATFORM", "webhook")
        monkeypatch.setattr(approval_context, "_get_unattended_approval_mode", lambda: "deny")

        result = check_all_command_guards(COMMAND, "local")

        assert result["approved"] is False

    def test_cron_deny_blocks_session_approved_dangerous_command(self, monkeypatch):
        _guard_env(monkeypatch)
        _, pattern_key, _ = detect_dangerous_command(COMMAND)
        monkeypatch.setenv("HERMES_SESSION_KEY", SESSION)
        approval_module.approve_session(SESSION, pattern_key)
        monkeypatch.setenv("HERMES_CRON_SESSION", "1")
        monkeypatch.setattr(approval_context, "_get_cron_approval_mode", lambda: "deny")

        assert check_all_command_guards(COMMAND, "local")["approved"] is False

    def test_cron_deny_still_blocks_without_any_allowlist(self, monkeypatch):
        _guard_env(monkeypatch)
        monkeypatch.setenv("HERMES_CRON_SESSION", "1")
        monkeypatch.setattr(approval_context, "_get_cron_approval_mode", lambda: "deny")

        assert check_all_command_guards(COMMAND, "local")["approved"] is False


class TestAllowlistKeepsVetoWhereAHumanIsPresent:
    def test_interactive_session_still_honors_permanent_allowlist(self, monkeypatch):
        _guard_env(monkeypatch)
        _always_allow_command()
        monkeypatch.setenv("HERMES_INTERACTIVE", "1")

        result = check_all_command_guards(COMMAND, "local")

        assert result["approved"] is True

    def test_cron_approve_mode_still_runs_allowlisted_command(self, monkeypatch):
        _guard_env(monkeypatch)
        _always_allow_command()
        monkeypatch.setenv("HERMES_CRON_SESSION", "1")
        monkeypatch.setattr(approval_context, "_get_cron_approval_mode", lambda: "approve")

        result = check_all_command_guards(COMMAND, "local")

        assert result["approved"] is True

    def test_cron_deny_leaves_benign_commands_alone(self, monkeypatch):
        _guard_env(monkeypatch)
        monkeypatch.setenv("HERMES_CRON_SESSION", "1")
        monkeypatch.setattr(approval_context, "_get_cron_approval_mode", lambda: "deny")

        result = check_all_command_guards("ls -la", "local")

        assert result["approved"] is True


class TestUnattendedDenyScopeStillBounded:
    def test_cron_session_var_marks_context_without_permanent_grant_leak(self, monkeypatch):
        """set_session_vars(cron_session=...) drives the same deny path as the env marker."""
        _guard_env(monkeypatch)
        _always_allow_command()
        monkeypatch.setattr(approval_context, "_get_cron_approval_mode", lambda: "deny")
        tokens = set_session_vars(cron_session="1")
        try:
            result = check_all_command_guards(COMMAND, "local")
        finally:
            clear_session_vars(tokens)

        assert result["approved"] is False
