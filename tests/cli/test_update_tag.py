"""Tests for ``hermes update --tag`` support (issue #23721)."""

import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**overrides):
    """Create a namespace mimicking argparse output for the update command."""
    defaults = dict(
        gateway=False,
        check=False,
        no_backup=False,
        backup=False,
        yes=False,
        branch=None,
        tag=None,
        force=False,
    )
    defaults.update(overrides)
    return type("Args", (), defaults)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestUpdateTagParser:
    """Verify ``--tag`` is registered on the update subparser."""

    def test_tag_argument_exists(self):
        from hermes_cli.subcommands.update import build_update_parser
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        build_update_parser(subparsers, cmd_update=lambda _: None)
        ns = parser.parse_args(["update", "--tag", "v0.16.0"])
        assert ns.tag == "v0.16.0"

    def test_tag_default_none(self):
        from hermes_cli.subcommands.update import build_update_parser
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        build_update_parser(subparsers, cmd_update=lambda _: None)
        ns = parser.parse_args(["update"])
        assert ns.tag is None

    def test_tag_and_branch_together_accepted_by_parser(self):
        """Parser itself doesn't enforce mutual exclusivity; cmd_update does."""
        from hermes_cli.subcommands.update import build_update_parser
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        build_update_parser(subparsers, cmd_update=lambda _: None)
        ns = parser.parse_args(["update", "--tag", "v0.16.0", "--branch", "main"])
        assert ns.tag == "v0.16.0"
        assert ns.branch == "main"


# ---------------------------------------------------------------------------
# cmd_update --tag + --branch mutual exclusion
# ---------------------------------------------------------------------------

class TestTagBranchMutualExclusion:
    """``--tag`` and ``--branch`` are mutually exclusive at runtime."""

    @patch("hermes_cli.main.sys")
    def test_tag_and_branch_exits(self, mock_sys):
        from hermes_cli.main import cmd_update

        mock_sys.exit.side_effect = SystemExit
        args = _make_args(tag="v0.16.0", branch="develop")

        with pytest.raises(SystemExit):
            cmd_update(args)


# ---------------------------------------------------------------------------
# _cmd_update_check_tag
# ---------------------------------------------------------------------------

class TestCmdUpdateCheckTag:
    """``hermes update --check --tag=TAG`` verifies the tag exists."""

    @patch("hermes_cli.main.subprocess.run")
    @patch("hermes_cli.main.PROJECT_ROOT", Path("/fake/root"))
    @patch("hermes_cli.config.detect_install_method", return_value="git")
    def test_check_tag_found(self, mock_detect, mock_run):
        from hermes_cli.main import _cmd_update_check_tag

        mock_run.side_effect = [
            MagicMock(returncode=0),  # git fetch origin --tags
            MagicMock(returncode=0, stdout="v0.16.0\n"),  # git tag -l v0.16.0
            MagicMock(returncode=0, stdout="abc1234567def\n"),  # git rev-list -1
        ]

        _cmd_update_check_tag("v0.16.0")

    @patch("hermes_cli.main.subprocess.run")
    @patch("hermes_cli.main.PROJECT_ROOT", Path("/fake/root"))
    @patch("hermes_cli.config.detect_install_method", return_value="git")
    def test_check_tag_not_found(self, mock_detect, mock_run):
        from hermes_cli.main import _cmd_update_check_tag

        mock_run.side_effect = [
            MagicMock(returncode=0),  # git fetch origin --tags
            MagicMock(returncode=0, stdout=""),  # git tag -l — not found
            MagicMock(returncode=0, stdout=""),  # git tag -l v0.16.0* — no suggestions
        ]

        with pytest.raises(SystemExit):
            _cmd_update_check_tag("v0.16.0")

    @patch("hermes_cli.config.detect_install_method", return_value="pip")
    def test_check_tag_pip_install_exits(self, mock_detect):
        from hermes_cli.main import _cmd_update_check_tag

        with pytest.raises(SystemExit):
            _cmd_update_check_tag("v0.16.0")

    @patch("hermes_cli.config.detect_install_method", return_value="docker")
    def test_check_tag_docker_exits(self, mock_detect):
        from hermes_cli.main import _cmd_update_check_tag

        with pytest.raises(SystemExit):
            _cmd_update_check_tag("v0.16.0")


# ---------------------------------------------------------------------------
# _update_via_zip --tag rejection
# ---------------------------------------------------------------------------

class TestUpdateViaZipTagRejection:
    """ZIP fallback should reject --tag just like it rejects non-main --branch."""

    @patch("hermes_cli.main.sys")
    def test_zip_rejects_tag(self, mock_sys):
        from hermes_cli.main import _update_via_zip

        mock_sys.exit.side_effect = SystemExit
        args = _make_args(tag="v0.16.0")

        with pytest.raises(SystemExit):
            _update_via_zip(args)


# ---------------------------------------------------------------------------
# _cmd_update_impl --tag path
# ---------------------------------------------------------------------------

class TestCmdUpdateImplTag:
    """Tag-based update inside _cmd_update_impl."""

    @patch("hermes_cli.main.subprocess.run")
    @patch("hermes_cli.main._stash_local_changes_if_needed", return_value=None)
    @patch("hermes_cli.main._restore_stashed_changes")
    @patch("hermes_cli.main._discard_stashed_changes")
    @patch("hermes_cli.main._invalidate_update_cache")
    @patch("hermes_cli.main._clear_bytecode_cache", return_value=0)
    @patch("hermes_cli.main._is_fork", return_value=False)
    @patch("hermes_cli.main._get_origin_url", return_value="https://github.com/NousResearch/hermes-agent")
    @patch("hermes_cli.main._discard_lockfile_churn")
    @patch("hermes_cli.main._run_pre_update_backup")
    @patch("hermes_cli.main._is_windows", return_value=False)
    @patch("hermes_cli.config.detect_install_method", return_value="git")
    def test_tag_checkout_basic(
        self, mock_detect, mock_win, mock_backup, mock_churn, mock_origin, mock_fork,
        mock_clear, mock_invalidate, mock_discard, mock_restore, mock_stash,
        mock_run, tmp_path,
    ):
        """Tag path: fetch tags, validate, checkout, skip branch logic."""
        # Create a fake .git dir so the git-path is taken
        (tmp_path / ".git").mkdir()

        with patch("hermes_cli.main.PROJECT_ROOT", tmp_path):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git fetch origin --tags
                MagicMock(returncode=0, stdout="v0.16.0\n"),  # git tag -l v0.16.0
                MagicMock(returncode=0),  # git checkout v0.16.0
            ]

            from hermes_cli.main import _cmd_update_impl

            args = _make_args(tag="v0.16.0")
            try:
                _cmd_update_impl(args, gateway_mode=True)
            except (SystemExit, Exception):
                pass

            calls = [str(c) for c in mock_run.call_args_list]
            assert any("fetch" in c and "--tags" in c for c in calls), \
                f"Expected 'git fetch origin --tags' in calls: {calls[:3]}"
            assert any("tag" in c and "v0.16.0" in c for c in calls), \
                f"Expected 'git tag -l v0.16.0' in calls: {calls[:3]}"


# ---------------------------------------------------------------------------
# Tag suggestion on typo
# ---------------------------------------------------------------------------

class TestTagSuggestion:
    """When a tag is not found, suggest similar tags."""

    @patch("hermes_cli.main.subprocess.run")
    @patch("hermes_cli.main.PROJECT_ROOT", Path("/fake/root"))
    @patch("hermes_cli.config.detect_install_method", return_value="git")
    def test_suggests_similar_tags(self, mock_detect, mock_run):
        from hermes_cli.main import _cmd_update_check_tag

        mock_run.side_effect = [
            MagicMock(returncode=0),  # git fetch origin --tags
            MagicMock(returncode=0, stdout=""),  # git tag -l v0.16 — not found
            MagicMock(returncode=0, stdout="v0.16.0\nv0.16.1\n"),  # suggestions
        ]

        with pytest.raises(SystemExit):
            _cmd_update_check_tag("v0.16")
