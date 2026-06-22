"""Tests for /whoami and /indicator slash command handlers in CLI context."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

from cli import HermesCLI


def _make_cli_stub(config=None):
    """Create a minimal HermesCLI stub for testing slash handlers."""
    cli = HermesCLI.__new__(HermesCLI)
    cli._sudo_state = None
    cli._secret_state = None
    cli._approval_state = None
    cli._clarify_state = None
    cli._clarify_freetext = False
    cli._command_running = False
    cli._agent_running = False
    cli._voice_recording = False
    cli._voice_processing = False
    cli._voice_mode = False
    cli._command_spinner_frame = lambda: "⟳"
    cli._tui_style_base = {
        "prompt": "#fff",
        "input-area": "#fff",
        "input-rule": "#aaa",
        "prompt-working": "#888 italic",
    }
    cli._app = SimpleNamespace(style=None)
    cli._invalidate = MagicMock()
    cli.config = config or {"display": {"tui_status_indicator": "kaomoji"}}
    return cli


class TestWhoamiCommand:
    @patch("cli._cprint")
    def test_whoami_prints_local_operator_info(self, mock_cp):
        cli = _make_cli_stub()
        cli._handle_whoami_command("/whoami")
        texts = [c.args[0] for c in mock_cp.call_args_list]
        combined = " ".join(texts)
        assert "local CLI/TUI" in combined
        assert "admin" in combined.lower()
        assert "all available" in combined.lower()


class TestIndicatorCommand:
    @patch("cli._cprint")
    def test_indicator_no_args_shows_current(self, mock_cp):
        cli = _make_cli_stub(config={"display": {"tui_status_indicator": "emoji"}})
        cli._handle_indicator_command("/indicator")
        texts = [c.args[0] for c in mock_cp.call_args_list]
        combined = " ".join(texts)
        assert "emoji" in combined
        assert "kaomoji" in combined  # listed as available

    @patch("cli.save_config_value", return_value=True)
    @patch("cli._cprint")
    def test_indicator_valid_style_saves(self, mock_cp, mock_save):
        cli = _make_cli_stub(config={"display": {"tui_status_indicator": "kaomoji"}})
        cli._handle_indicator_command("/indicator ascii")
        mock_save.assert_called_once_with("display.tui_status_indicator", "ascii")
        texts = [c.args[0] for c in mock_cp.call_args_list]
        combined = " ".join(texts)
        assert "ascii" in combined

    @patch("cli._cprint")
    def test_indicator_invalid_style_shows_error(self, mock_cp):
        cli = _make_cli_stub()
        cli._handle_indicator_command("/indicator rainbow")
        texts = [c.args[0] for c in mock_cp.call_args_list]
        combined = " ".join(texts).lower()
        assert "rainbow" in combined or "unknown" in combined

    @patch("cli.save_config_value", return_value=True)
    @patch("cli._cprint")
    def test_indicator_all_valid_styles(self, mock_cp, mock_save):
        """All four documented styles should be accepted."""
        for style in ("kaomoji", "emoji", "unicode", "ascii"):
            mock_cp.reset_mock()
            mock_save.reset_mock()
            cli = _make_cli_stub(config={"display": {}})
            cli._handle_indicator_command(f"/indicator {style}")
            mock_save.assert_called_once_with("display.tui_status_indicator", style)
