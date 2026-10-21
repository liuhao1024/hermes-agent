"""Issue #142314: a failed cua-driver MCP handshake must quote the driver's own stderr
words (the "incompatible daemon: contract version ..." line), not just the TaskGroup
wrapper that str() renders as "unhandled errors in a TaskGroup (1 sub-exception)"."""

from __future__ import annotations

import sys
import tempfile

import pytest


class TestSetupErrorSurfacesDriverStderr:
    def test_failure_detail_flattens_the_task_group_wrapper(self):
        from tools.computer_use.cua_backend_session import _setup_failure_detail

        group = ExceptionGroup(
            "unhandled errors in a TaskGroup (1 sub-exception)",
            [
                RuntimeError(
                    "cua-driver-rs: invalid daemon response: incompatible daemon: "
                    "contract version 0.8.0 does not match SDK 0.7.0"
                )
            ],
        )
        detail = _setup_failure_detail(group, "")
        assert "contract version 0.8.0 does not match SDK 0.7.0" in detail
        # Only the leaf messages are quoted — the wrapper line carries nothing actionable.
        assert "unhandled errors in a TaskGroup" not in detail

    def test_failure_detail_appends_the_stderr_tail(self):
        from tools.computer_use.cua_backend_session import _setup_failure_detail

        detail = _setup_failure_detail(RuntimeError("boom"), "incompatible daemon line")
        assert detail == "boom — driver stderr tail: incompatible daemon line"

    def test_stderr_tail_keeps_only_the_last_bytes(self):
        from tools.computer_use.cua_backend_session import _stderr_tail

        with tempfile.TemporaryFile() as sink:
            sink.write(
                b"early-noise\n"
                + b"x" * 8192
                + b"\nincompatible daemon: contract mismatch\n"
            )
            sink.flush()
            tail = _stderr_tail(sink)
            assert tail.endswith("incompatible daemon: contract mismatch")
            assert "early-noise" not in tail

    def test_start_quotes_the_driver_stderr_on_a_failed_handshake(self, monkeypatch):
        from tools.computer_use import cua_backend as _cb
        from tools.computer_use import cua_backend_driver as _driver
        from tools.computer_use import cua_backend_session as cbs
        from tools.environments import local as _local

        exit_script = (
            "import sys\n"
            "sys.stderr.write('cua-driver-rs: invalid daemon response: incompatible daemon: "
            "contract version 0.8.0 does not match SDK 0.7.0\\n')\n"
            "sys.exit(1)\n"
        )
        monkeypatch.setattr(_driver, "resolve_cua_driver_cmd", lambda: sys.executable)
        monkeypatch.setattr(_cb, "sandbox_mcp_invocation", lambda: None)
        monkeypatch.setattr(_cb, "cua_driver_child_env", dict)
        monkeypatch.setattr(
            _driver, "_resolve_mcp_invocation", lambda cmd: (cmd, ["-c", exit_script])
        )
        monkeypatch.setattr(_local, "_sanitize_subprocess_env", lambda env: env)

        bridge = cbs._AsyncBridge()
        session = cbs._CuaDriverSession(bridge)
        bridge.start()
        try:
            with pytest.raises(RuntimeError) as excinfo:
                session.start()
            msg = str(excinfo.value)
            assert msg.startswith("cua-driver session setup failed: ")
            assert (
                "incompatible daemon: contract version 0.8.0 does not match SDK 0.7.0"
                in msg
            )
            assert "driver stderr tail" in msg
        finally:
            bridge.stop()
