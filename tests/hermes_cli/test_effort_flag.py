"""Tests for the --effort CLI flag on hermes chat."""

import pytest
from unittest.mock import patch, MagicMock


class TestEffortParserFlag:
    """Test that --effort is registered in the chat parser."""

    def test_effort_flag_in_chat_parser(self):
        from hermes_cli._parser import build_top_level_parser

        parser, _, chat_parser = build_top_level_parser()
        # Parse with --effort
        args = chat_parser.parse_args(["-q", "hello", "--effort", "high"])
        assert args.effort == "high"

    def test_effort_flag_default_none(self):
        from hermes_cli._parser import build_top_level_parser

        _, _, chat_parser = build_top_level_parser()
        args = chat_parser.parse_args(["-q", "hello"])
        assert args.effort is None

    def test_effort_all_valid_values(self):
        from hermes_cli._parser import build_top_level_parser

        _, _, chat_parser = build_top_level_parser()
        for level in ("none", "minimal", "low", "medium", "high", "xhigh"):
            args = chat_parser.parse_args(["-q", "hello", "--effort", level])
            assert args.effort == level


class TestEffortPassthrough:
    """Test that --effort flows from args to cli_main kwargs."""

    def test_effort_in_kwargs(self):
        from hermes_cli._parser import build_top_level_parser

        _, _, chat_parser = build_top_level_parser()
        args = chat_parser.parse_args(["-q", "hello", "--effort", "xhigh"])

        # Simulate the kwargs construction from cmd_chat
        kwargs = {
            "model": args.model,
            "query": args.query,
            "effort": getattr(args, "effort", None),
        }
        assert kwargs["effort"] == "xhigh"

    def test_effort_absent_when_not_provided(self):
        from hermes_cli._parser import build_top_level_parser

        _, _, chat_parser = build_top_level_parser()
        args = chat_parser.parse_args(["-q", "hello"])

        kwargs = {
            "model": args.model,
            "query": args.query,
            "effort": getattr(args, "effort", None),
        }
        # None gets filtered out in cmd_chat's kwargs filtering
        assert kwargs["effort"] is None


class TestEffortOverride:
    """Test that --effort overrides reasoning_config in cli.main()."""

    def test_valid_effort_overrides_reasoning_config(self):
        from hermes_constants import parse_reasoning_effort

        # "high" should produce {"enabled": True, "effort": "high"}
        result = parse_reasoning_effort("high")
        assert result == {"enabled": True, "effort": "high"}

        # "none" should produce {"enabled": False}
        result = parse_reasoning_effort("none")
        assert result == {"enabled": False}

    def test_invalid_effort_returns_none(self):
        from hermes_constants import parse_reasoning_effort

        result = parse_reasoning_effort("invalid_value")
        assert result is None

    def test_effort_none_does_not_override(self):
        """When effort=None (flag not provided), reasoning_config should stay at default."""
        from hermes_constants import parse_reasoning_effort

        result = parse_reasoning_effort(None)
        assert result is None

    @patch("cli.HermesCLI")
    def test_effort_flag_applied_to_cli(self, mock_hermes_cli):
        """Verify that effort flag overrides reasoning_config after CLI construction."""
        mock_cli = MagicMock()
        mock_cli.reasoning_config = {"enabled": True, "effort": "medium"}  # default
        mock_hermes_cli.return_value = mock_cli

        from hermes_constants import parse_reasoning_effort

        effort = "xhigh"
        effort_config = parse_reasoning_effort(effort)
        assert effort_config is not None
        # Simulate the override
        mock_cli.reasoning_config = effort_config
        assert mock_cli.reasoning_config == {"enabled": True, "effort": "xhigh"}
