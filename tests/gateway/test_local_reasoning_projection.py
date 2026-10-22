from types import SimpleNamespace

from gateway.config import Platform
from gateway.run_turn_persistence import GatewayTurnPersistenceMixin


def test_local_final_does_not_receive_platform_reasoning_projection(monkeypatch):
    """Local viewers receive reasoning on their event stream, never in the final text."""
    import gateway.run as gateway_run

    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {"display": {"show_reasoning": True}})
    result = {"last_reasoning": "private chain"}
    runner = GatewayTurnPersistenceMixin()

    local_response = runner._hmwa_prepend_reasoning(
        result, "final answer", SimpleNamespace(platform=Platform.LOCAL), False,
    )
    telegram_response = runner._hmwa_prepend_reasoning(
        result, "final answer", SimpleNamespace(platform=Platform.TELEGRAM), False,
    )

    assert local_response == "final answer"
    assert "private chain" in telegram_response
    assert telegram_response.endswith("final answer")


def _acp_projection_case(monkeypatch, live_flag):
    """An ACP launch whose session flag ``live_flag`` the adapter/viewer last set (None: unset)."""
    import gateway.run as gateway_run
    import gateway.run_turn_persistence as persistence
    import gateway.session_setting_commands as settings

    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {"display": {"show_reasoning": True}})
    monkeypatch.setattr(persistence, "_acp_launch", lambda runner, source: True)
    live = SimpleNamespace(show_reasoning=live_flag)
    monkeypatch.setattr(settings, "local_live", lambda runner, source: live)

    return GatewayTurnPersistenceMixin()._hmwa_prepend_reasoning(
        {"last_reasoning": "editor chain"}, "final answer", SimpleNamespace(platform=Platform.LOCAL), False,
    )


def test_acp_live_reasoning_session_does_not_get_the_projected_block(monkeypatch):
    """Live reasoning streamed the block as reasoning.delta thought chunks already (#141919)."""
    assert _acp_projection_case(monkeypatch, True) == "final answer"


def test_acp_hidden_reasoning_session_keeps_the_projected_block(monkeypatch):
    """A viewer that hid reasoning gets the block in the reply: its editor showed no stream."""
    assert "editor chain" in _acp_projection_case(monkeypatch, False)


def test_acp_unflagged_session_keeps_the_projected_block(monkeypatch):
    """An attach whose live-reasoning request failed keeps the projected block (flag unset)."""
    assert "editor chain" in _acp_projection_case(monkeypatch, None)
