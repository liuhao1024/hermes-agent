"""Tests for gateway.platforms.helpers — MessageDeduplicator persistence."""

import json
import time
from pathlib import Path

from gateway.platforms.helpers import MessageDeduplicator


class TestMessageDeduplicatorPersistence:
    """Verify that MessageDeduplicator can persist and restore state via state_path."""

    def test_no_state_path_works_as_before(self, tmp_path: Path) -> None:
        """Without state_path, behaviour is identical to the original."""
        dedup = MessageDeduplicator(ttl_seconds=300)
        assert dedup.is_duplicate("msg-1") is False
        assert dedup.is_duplicate("msg-1") is True
        assert dedup.is_duplicate("msg-2") is False

    def test_state_path_loads_on_init(self, tmp_path: Path) -> None:
        """Pre-populated state file is loaded on construction."""
        state_file = tmp_path / "dedup.json"
        state_file.write_text(
            json.dumps({"msg-old": time.time() - 10}),
            encoding="utf-8",
        )
        dedup = MessageDeduplicator(ttl_seconds=300, state_path=state_file)
        assert dedup.is_duplicate("msg-old") is True

    def test_expired_entries_not_loaded(self, tmp_path: Path) -> None:
        """Entries older than TTL are pruned on load."""
        state_file = tmp_path / "dedup.json"
        state_file.write_text(
            json.dumps({"msg-stale": time.time() - 600}),
            encoding="utf-8",
        )
        dedup = MessageDeduplicator(ttl_seconds=300, state_path=state_file)
        assert dedup.is_duplicate("msg-stale") is False

    def test_flush_persists_state(self, tmp_path: Path) -> None:
        """flush() writes current state to disk."""
        state_file = tmp_path / "dedup.json"
        dedup = MessageDeduplicator(ttl_seconds=300, state_path=state_file)
        dedup.is_duplicate("msg-1")
        dedup.is_duplicate("msg-2")
        dedup.flush()
        assert state_file.exists()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert "msg-1" in data
        assert "msg-2" in data

    def test_periodic_save(self, tmp_path: Path) -> None:
        """State is saved after save_every new entries."""
        state_file = tmp_path / "dedup.json"
        dedup = MessageDeduplicator(
            ttl_seconds=300, state_path=state_file, save_every=3,
        )
        # First 2 entries: no save yet
        dedup.is_duplicate("m1")
        dedup.is_duplicate("m2")
        assert not state_file.exists()
        # 3rd entry triggers save
        dedup.is_duplicate("m3")
        assert state_file.exists()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert len(data) == 3

    def test_corrupted_state_file_ignored(self, tmp_path: Path) -> None:
        """A corrupted state file doesn't crash the deduplicator."""
        state_file = tmp_path / "dedup.json"
        state_file.write_text("NOT VALID JSON {{{", encoding="utf-8")
        dedup = MessageDeduplicator(ttl_seconds=300, state_path=state_file)
        # Should still work, just with empty state
        assert dedup.is_duplicate("msg-new") is False

    def test_missing_state_file_ok(self, tmp_path: Path) -> None:
        """Missing state file is fine — starts with empty state."""
        state_file = tmp_path / "nonexistent.json"
        dedup = MessageDeduplicator(ttl_seconds=300, state_path=state_file)
        assert dedup.is_duplicate("msg-new") is False

    def test_flush_without_state_path_is_noop(self, tmp_path: Path) -> None:
        """flush() does nothing when state_path is None."""
        dedup = MessageDeduplicator(ttl_seconds=300)
        dedup.is_duplicate("msg-1")
        dedup.flush()  # Should not raise

    def test_roundtrip_persistence(self, tmp_path: Path) -> None:
        """Full roundtrip: populate → flush → new instance → verify loaded."""
        state_file = tmp_path / "dedup.json"
        dedup1 = MessageDeduplicator(ttl_seconds=300, state_path=state_file)
        for i in range(10):
            dedup1.is_duplicate(f"msg-{i}")
        dedup1.flush()

        dedup2 = MessageDeduplicator(ttl_seconds=300, state_path=state_file)
        for i in range(10):
            assert dedup2.is_duplicate(f"msg-{i}") is True, f"msg-{i} should be seen"
        # New message should not be duplicate
        assert dedup2.is_duplicate("msg-new") is False
