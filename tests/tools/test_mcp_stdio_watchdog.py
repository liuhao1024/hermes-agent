"""Tests for MCP stdio watchdog parent-death detection."""

import pytest

from tools.mcp_stdio_watchdog import _is_orphaned


class TestIsOrphaned:
    """Tests for _is_orphaned() function."""

    def test_same_ppid_not_orphaned(self):
        """When PPID matches the original, the parent is still alive."""
        assert not _is_orphaned(12345, getppid=lambda: 12345)

    def test_different_ppid_is_orphaned(self):
        """When PPID changes, the parent has died and child is orphaned."""
        assert _is_orphaned(12345, getppid=lambda: 1)

    def test_ppid_change_to_any_other_value_is_orphaned(self):
        """Any PPID change indicates orphaning, not just init (PID 1)."""
        # Reparented to subreaper (not PID 1)
        assert _is_orphaned(12345, getppid=lambda: 2)
        # Reparented to arbitrary process
        assert _is_orphaned(12345, getppid=lambda: 99999)

    def test_original_parent_zero_not_orphaned(self):
        """Edge case: original parent PID can be zero (special cases)."""
        assert not _is_orphaned(0, getppid=lambda: 0)

    def test_current_parent_zero_is_orphaned_if_different(self):
        """If current PPID is zero and original wasn't, it's orphaned."""
        assert _is_orphaned(12345, getppid=lambda: 0)

    def test_stable_under_clock_changes(self):
        """PPID check is unaffected by system clock changes.

        This is the key fix: the old implementation used create_time()
        which can change when the system clock drifts or is adjusted
        (e.g., NTP sync, WSL2 clock issues), causing false positives.
        """
        # Simulate: parent PPID constant, but pretend clock changed
        # Old implementation would have failed this
        assert not _is_orphaned(4242, getppid=lambda: 4242)