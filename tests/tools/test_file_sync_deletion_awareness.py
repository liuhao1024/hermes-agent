"""Sync-back must respect host-side deletions (#144303).

Covers the two resurrection paths: a stale remote orphan left behind by a prior
session (its committed deletion is recorded in the host-side tombstone file), and
a file pushed earlier this session but deleted on the host before the deletion
could propagate (pushed-hash gate). A genuinely new remote file must still land.
"""

import hashlib
import io
import tarfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.environments.file_sync import (
    FileSyncManager,
    _deleted_tombstone_path,
    _load_deleted_remote_paths,
    _sha256_file,
)


def _make_tar(files: dict[str, bytes], dest: Path):
    with tarfile.open(dest, "w") as tar:
        for arcname, content in files.items():
            info = tarfile.TarInfo(name=arcname)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))


def _download_fn(files: dict[str, bytes]):
    def download(dest: Path):
        _make_tar(files, dest)
    return download


def _write_file(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.environments.file_sync.get_hermes_home", lambda: tmp_path)
    return tmp_path


def _manager(tmp_path: Path, host_files: dict[str, bytes], remote_files: dict[str, bytes],
             pushed: dict[str, str] | None = None) -> FileSyncManager:
    """Wire a manager over a real host tree and a fake remote tar.

    Seeds one pushed hash from the host tree so sync_back doesn't early-return on
    the "nothing previously pushed" guard unless the test provides its own.
    """
    mapping = [(_write_file(tmp_path / "host" / name, content), f"/root/.hermes/{name}")
               for name, content in host_files.items()]
    manager = FileSyncManager(
        get_files_fn=lambda: list(mapping),
        upload_fn=MagicMock(),
        delete_fn=MagicMock(),
        bulk_upload_fn=MagicMock(),
        bulk_download_fn=_download_fn(remote_files))
    if pushed:
        manager._pushed_hashes.update(pushed)
    elif mapping:
        first_host, first_remote = mapping[0]
        manager._pushed_hashes[first_remote] = _sha256_file(first_host)
    return manager


def test_prior_session_orphan_is_not_resurrected(hermes_home, tmp_path):
    deleted = "/root/.hermes/skills/demo/templates/a.md"
    tombstone = _deleted_tombstone_path(hermes_home)
    tombstone.parent.mkdir(parents=True, exist_ok=True)
    tombstone.write_text(deleted + "\n", encoding="utf-8")
    manager = _manager(tmp_path,
                       host_files={"skills/demo/SKILL.md": b"skill"},
                       remote_files={"root/.hermes/skills/demo/templates/a.md": b"zombie"})
    manager.sync_back()
    assert not (tmp_path / "host" / "skills/demo/templates/a.md").exists()


def test_new_remote_file_still_applied(hermes_home, tmp_path):
    _deleted_tombstone_path(hermes_home).write_text("/root/.hermes/gone.txt\n", encoding="utf-8")
    manager = _manager(tmp_path,
                       host_files={"skills/demo/SKILL.md": b"skill"},
                       remote_files={"root/.hermes/skills/demo/new.txt": b"fresh"})
    manager.sync_back()
    assert (tmp_path / "host" / "skills/demo/new.txt").read_bytes() == b"fresh"


def test_host_deleted_after_push_is_not_resurrected(hermes_home, tmp_path):
    name = "skills/demo/templates/a.md"
    manager = _manager(tmp_path,
                       host_files={"skills/demo/SKILL.md": b"skill"},
                       remote_files={"root/.hermes/skills/demo/templates/a.md": b"remote edit"},
                       pushed={f"/root/.hermes/{name}": hashlib.sha256(b"original").hexdigest()})
    manager.sync_back()
    assert not (tmp_path / "host" / name).exists()


def test_committed_deletion_records_tombstone(hermes_home, tmp_path):
    alive = _write_file(tmp_path / "host" / "keep.md", b"keep")
    _write_file(tmp_path / "host" / "doomed.md", b"doom")
    mapping = [(alive, "/root/.hermes/keep.md")]
    calls: list[str] = []
    manager = FileSyncManager(
        get_files_fn=lambda: list(mapping),
        upload_fn=MagicMock(),
        delete_fn=lambda paths: calls.extend(paths),
        bulk_upload_fn=MagicMock(),
        bulk_download_fn=MagicMock())
    manager._synced_files["/root/.hermes/doomed.md"] = (0.0, 4)
    manager.sync(force=True)
    assert "/root/.hermes/doomed.md" in calls
    assert "/root/.hermes/doomed.md" in _load_deleted_remote_paths(hermes_home)


def test_vacuum_drops_tombstones_for_restored_paths(hermes_home, tmp_path):
    restored = "/root/.hermes/skills/demo/templates/back.md"
    still_gone = "/root/.hermes/skills/demo/templates/gone.md"
    _deleted_tombstone_path(hermes_home).write_text(f"{restored}\n{still_gone}\n", encoding="utf-8")
    manager = _manager(tmp_path,
                       host_files={"skills/demo/templates/back.md": b"back"},
                       remote_files={})
    manager.sync_back()
    assert _load_deleted_remote_paths(hermes_home) == {still_gone}


def test_tombstone_does_not_block_update_of_existing_host_file(hermes_home, tmp_path):
    name = "skills/demo/templates/a.md"
    remote = f"/root/.hermes/{name}"
    manager = _manager(tmp_path,
                       host_files={name: b"host version"},
                       remote_files={f"root/.hermes/{name}": b"remote version"},
                       pushed={remote: hashlib.sha256(b"original").hexdigest()})
    manager.sync_back()
    assert (tmp_path / "host" / name).read_bytes() == b"remote version"
