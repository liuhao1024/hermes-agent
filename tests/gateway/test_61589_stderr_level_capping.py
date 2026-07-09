"""Test _resolve_stderr_level() TTY-based stderr level capping.

This validates the fix for #61589: when stderr is redirected (launchd,
systemd, Docker), cap at CRITICAL to avoid unbounded log growth.
"""
import logging
import os
import sys

import pytest

from gateway.run import _resolve_stderr_level


class MockTTY:
    """Context manager to temporarily mock sys.stderr.isatty()."""

    def __init__(self, is_tty: bool):
        self.is_tty = is_tty
        self.orig_isatty = None

    def __enter__(self):
        self.orig_isatty = sys.stderr.isatty
        sys.stderr.isatty = lambda: self.is_tty
        return self

    def __exit__(self, *args):
        sys.stderr.isatty = self.orig_isatty


def test_resolve_stderr_level_tty_respects_base_level():
    """When stderr is a TTY, return the base level unchanged."""
    with MockTTY(is_tty=True):
        assert _resolve_stderr_level(logging.WARNING) == logging.WARNING
        assert _resolve_stderr_level(logging.INFO) == logging.INFO
        assert _resolve_stderr_level(logging.DEBUG) == logging.DEBUG


def test_resolve_stderr_level_non_tty_caps_at_critical():
    """When stderr is redirected (non-TTY), cap at CRITICAL."""
    with MockTTY(is_tty=False):
        # All base levels below CRITICAL are capped at CRITICAL
        assert _resolve_stderr_level(logging.DEBUG) == logging.CRITICAL
        assert _resolve_stderr_level(logging.INFO) == logging.CRITICAL
        assert _resolve_stderr_level(logging.WARNING) == logging.CRITICAL
        # CRITICAL and above pass through
        assert _resolve_stderr_level(logging.CRITICAL) == logging.CRITICAL


def test_resolve_stderr_level_env_var_override():
    """HERMES_STDERR_LOG_LEVEL env var takes precedence over TTY detection."""
    os.environ["HERMES_STDERR_LOG_LEVEL"] = "DEBUG"

    try:
        with MockTTY(is_tty=False):
            # Env var overrides non-TTY cap
            assert _resolve_stderr_level(logging.WARNING) == logging.DEBUG

        with MockTTY(is_tty=True):
            # Env var also overrides TTY behavior
            assert _resolve_stderr_level(logging.WARNING) == logging.DEBUG
    finally:
        os.environ.pop("HERMES_STDERR_LOG_LEVEL", None)


def test_resolve_stderr_level_invalid_env_var_ignored():
    """Invalid HERMES_STDERR_LOG_LEVEL values are ignored with a warning."""
    os.environ["HERMES_STDERR_LOG_LEVEL"] = "INVALID_LEVEL"

    try:
        with MockTTY(is_tty=False):
            # Falls back to CRITICAL when env var is invalid
            assert _resolve_stderr_level(logging.WARNING) == logging.CRITICAL

        with MockTTY(is_tty=True):
            # Falls back to base level when env var is invalid
            assert _resolve_stderr_level(logging.WARNING) == logging.WARNING
    finally:
        os.environ.pop("HERMES_STDERR_LOG_LEVEL", None)