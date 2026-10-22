"""sed's ``w`` command writes the pattern space to a file using neither ``-i`` nor
shell redirection syntax, so it slipped past every sensitive-write rule
(redirection / tee / cp-mv-install / sed -i / perl-ruby -i) until #143198.
These tests pin the same target set those rules gate, plus the cron-session
fail-closed contract the detector feeds.
"""

import pytest
from unittest.mock import patch as mock_patch

import tools.approval as approval_module
from gateway.session_context import reset_session_vars
from tools.approval import check_dangerous_command
from tools.approval_detection import detect_dangerous_command


@pytest.fixture(autouse=True)
def _clear_approval_state():
    approval_module._permanent_approved.clear()
    approval_module.clear_session("default")
    approval_module.clear_session("test-session")
    reset_session_vars()
    yield
    approval_module._permanent_approved.clear()
    approval_module.clear_session("default")
    approval_module.clear_session("test-session")
    reset_session_vars()


class TestSedWriteCommandPattern:
    """`w <file>` as a sed command and as an `s///w` flag must gate the
    sensitive targets the in-place rules already gate."""

    @pytest.mark.parametrize(
        "command",
        (
            # bare w command, quoted program (the #143198 repro shape)
            'sed -n "w ~/.ssh/config" ~/payload',
            'sed -n "w $HOME/.ssh/authorized_keys" "$HOME/payload"',
            # s command's w flag
            "sed 's/x/y/w ~/.bashrc' input.txt",
            # -e spelling
            "sed -e 'w ~/.netrc' -e p /etc/hosts",
            # Hermes config/env
            "sed -n 'w ~/.hermes/config.yaml' x",
            "sed -n 'w ~/.hermes/.env' x",
            # system config
            "sed -n 'w /etc/cron.d/backdoor' x",
        ),
    )
    def test_write_command_flagged(self, command):
        dangerous, key, _desc = detect_dangerous_command(command)
        assert dangerous is True, command
        assert key is not None, command

    @pytest.mark.parametrize(
        "command",
        (
            # reading a sensitive file stays safe: `world` has no `w` word boundary
            "sed 's/hello/world/' ~/.ssh/config",
            "sed -n '/^Port/p' ~/.ssh/config",
            # `w` before the pipe is echo's argument, not part of the sed command
            "echo 'w ~/.ssh/config' | sed 's/a/b/'",
            # replacement text containing ` w ` whose tail is not a sensitive target
            "sed 's/ w /x/' ~/.ssh/config",
            # w to an ordinary file is not gated
            "sed -n '1,10w /tmp/out.txt' input",
        ),
    )
    def test_reads_and_plain_targets_safe(self, command):
        dangerous, key, _desc = detect_dangerous_command(command)
        assert dangerous is False, command
        assert key is None, command


class TestSedWriteCommandCronFailClosed:
    """The documented cron contract: a flagged command is denied, never silent."""

    def test_write_command_blocked_in_cron_deny_mode(self, monkeypatch):
        monkeypatch.setenv("HERMES_CRON_SESSION", "1")
        monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)
        monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
        monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)

        with mock_patch("tools.approval_context._get_cron_approval_mode", return_value="deny"):
            result = check_dangerous_command(
                'sed -n "w ~/.ssh/config" ~/payload', "local"
            )
            assert not result["approved"]
            assert "BLOCKED" in result["message"]
