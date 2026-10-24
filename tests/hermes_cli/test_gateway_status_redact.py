"""`hermes gateway status` must not leak credential arguments (#143938).

systemctl's status output renders the unit's whole CGroup tree with every child's full
command line, so an MCP server launched with ``--api-key <live-key>`` would print the key
verbatim. The gateway captures the output and masks credential-flag operands before any
of it reaches the terminal — these tests pin that fail-closed behaviour.
"""

import subprocess

import pytest

from hermes_cli.gateway import systemd_status
from hermes_cli.gateway_status_redact import mask_credential_flags

SECRET = "5f2a9c0e7b1d4f8a3c6e9b0d2f7a4e5c"

SYSTEMD_STATUS_OUTPUT = f"""● hermes-gateway.service - Hermes Agent Gateway
     Loaded: loaded (/home/ops/.config/systemd/user/hermes-gateway.service; enabled)
     Active: active (running) since Fri 2026-10-23 09:00:00 UTC; 1 day ago
   Main PID: 4242 (python)
      Tasks: 8 (limit: 38000)
     CGroup: /user.slice/user-501.slice/user@501.service/hermes-gateway.service
             ├─4242 /usr/bin/python -m hermes_cli.main gateway run
             └─4300 node /opt/mcp/server.js --email ops@example.com --api-key {SECRET}
"""


class TestMaskCredentialFlags:
    @pytest.mark.parametrize(
        "flag",
        ["api-key", "api_key", "apikey", "token", "secret", "password", "passwd"],
    )
    @pytest.mark.parametrize("separator", [" ", "="])
    def test_credential_operand_is_masked(self, flag, separator):
        result = mask_credential_flags(
            f"node server.js --{flag}{separator}{SECRET} --port 8003"
        )
        assert SECRET not in result
        assert f"--{flag}{separator}***" in result
        assert "--port 8003" in result

    @pytest.mark.parametrize(
        "argv",
        [
            "server --token-count 42",
            "server --api-key-file /keys/key",
            "server --password-policy strong",
            "server --max-tokens 100",
            "server --email ops@example.com",
            "server --token",
        ],
    )
    def test_neighbouring_flags_are_preserved(self, argv):
        assert mask_credential_flags(argv) == argv

    def test_masking_is_idempotent(self):
        once = mask_credential_flags(f"server --api-key {SECRET}")
        assert mask_credential_flags(once) == once

    def test_quoted_operand_is_masked(self):
        result = mask_credential_flags(
            'server --password "synthetic password with spaces"'
        )
        assert "synthetic password" not in result


class TestSystemdStatusRedaction:
    @pytest.fixture
    def _installed_service(self, monkeypatch, tmp_path):
        unit = tmp_path / "hermes-gateway.service"
        unit.touch()
        monkeypatch.setattr(
            "hermes_cli.gateway.get_systemd_unit_path", lambda system=False: unit
        )
        monkeypatch.setattr(
            "hermes_cli.gateway.has_conflicting_systemd_units", lambda: False
        )
        monkeypatch.setattr("hermes_cli.gateway.has_legacy_hermes_units", lambda: False)
        monkeypatch.setattr(
            "hermes_cli.gateway.systemd_unit_is_current", lambda system=False: True
        )
        monkeypatch.setattr(
            "hermes_cli.gateway._read_systemd_unit_properties",
            lambda system=False, properties=(): {
                "ActiveState": "active",
                "SubState": "running",
            },
        )
        monkeypatch.setattr(
            "hermes_cli.gateway.get_systemd_linger_status",
            lambda username=None: (None, "stub"),
        )
        monkeypatch.setattr("hermes_cli.gateway._print_runtime_health", lambda: None)

        def fake_run_systemctl(args, *, system=False, **kwargs):
            if args[0] == "status":
                return subprocess.CompletedProcess(
                    args, 0, stdout=SYSTEMD_STATUS_OUTPUT, stderr=""
                )
            if args[0] == "is-active":
                return subprocess.CompletedProcess(
                    args, 0, stdout="active\n", stderr=""
                )
            raise AssertionError(f"unexpected systemctl call: {args}")

        monkeypatch.setattr("hermes_cli.gateway._run_systemctl", fake_run_systemctl)

    def test_status_output_masks_the_live_key(self, _installed_service, capsys):
        systemd_status()
        captured = capsys.readouterr()
        assert SECRET not in captured.out
        assert "--api-key ***" in captured.out
        assert "ops@example.com" in captured.out
        assert "Main PID: 4242" in captured.out

    def test_status_stderr_is_masked_too(self, _installed_service, monkeypatch, capsys):
        def fake_run_systemctl(args, *, system=False, **kwargs):
            if args[0] == "status":
                return subprocess.CompletedProcess(
                    args, 0, stdout="", stderr=f"Warning: unit env --api-key {SECRET}\n"
                )
            if args[0] == "is-active":
                return subprocess.CompletedProcess(
                    args, 0, stdout="inactive\n", stderr=""
                )
            raise AssertionError(f"unexpected systemctl call: {args}")

        monkeypatch.setattr("hermes_cli.gateway._run_systemctl", fake_run_systemctl)
        systemd_status()
        captured = capsys.readouterr()
        assert SECRET not in captured.err
        assert "--api-key ***" in captured.err
