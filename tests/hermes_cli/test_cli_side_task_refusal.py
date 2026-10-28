"""A refused side task prints one line — never a traceback into the TUI (#146005).

The owner refuses ``prompt.btw`` / ``prompt.background`` for a socket that holds no subscription
for the session (``permission_denied``). The handlers must render that verdict as one line, not
let the ``GatewayRPCError`` escape into the prompt_toolkit key handler.
"""
import types

import pytest

from hermes_cli.gateway_client import GatewayRPCError


@pytest.mark.parametrize(("handler", "label"), [
    ("_handle_btw_command", "/btw"),
    ("_handle_background_command", "/bg"),
])
def test_a_refused_side_task_prints_one_line_and_does_not_raise(handler, label, monkeypatch):
    import cli
    from hermes_cli import gateway_side
    from hermes_cli.cli_commands_mixin import CLICommandsMixin

    painted = []
    monkeypatch.setattr(cli, "_cprint", lambda text: painted.append(str(text)))
    monkeypatch.setattr(gateway_side, "connection_for", lambda _cli: object())

    def refused(*_a, **_k):
        raise GatewayRPCError("permission_denied")
    monkeypatch.setattr(gateway_side, "start", refused)

    host = types.SimpleNamespace(_background_task_counter=0, session_id="local-side")
    getattr(CLICommandsMixin, handler)(host, f"{label} hello there")

    assert len(painted) == 1
    line = painted[0]
    assert line.startswith(f"  {label}:")
    # The refusal says what it means and what to do — no traceback, no bare error class.
    assert "no longer attached" in line
    assert "GatewayRPCError" not in line
