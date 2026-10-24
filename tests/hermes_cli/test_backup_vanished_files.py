"""Tests for the backup scan-to-archive TOCTOU window (#144066).

Hermes keeps running while ``hermes backup`` archives: it deletes its own short-lived files
(``logs/process-results/proc_*.json``, ``cron/admissions/*.json``, ...) after the scan listed
them. A file that vanished before the archive opened it is not missing from the archive — it no
longer exists — so it must be skipped instead of counted as an error that fails the run (exit 1)
and blocks ``--keep`` pruning. Real read failures (permissions, I/O) still fail the run.
"""

from argparse import Namespace
from pathlib import Path
from zipfile import ZipFile

import pytest

from tests.hermes_cli.test_backup import _make_hermes_tree


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    _make_hermes_tree(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


def _delete_after_scan(monkeypatch, backup_mod, victim: Path) -> None:
    """Wrap _iter_backup_files so *victim* is unlinked after the scan has listed it but
    before the archive phase opens it — the exact race from the issue."""
    real_iter = backup_mod._iter_backup_files

    def iter_deleting(*args, **kwargs):
        entries = list(real_iter(*args, **kwargs))
        assert any(abs_path == victim for abs_path, _ in entries), "victim missing from scan"
        victim.unlink()
        return entries

    monkeypatch.setattr(backup_mod, "_iter_backup_files", iter_deleting)


def test_run_backup_skips_plain_file_deleted_between_scan_and_archive(
        hermes_home, tmp_path, monkeypatch, capsys):
    from hermes_cli import backup as backup_mod

    victim = hermes_home / "logs" / "process-results" / "proc_1.json"
    victim.parent.mkdir(parents=True)
    victim.write_text("{}")
    _delete_after_scan(monkeypatch, backup_mod, victim)

    out_zip = tmp_path / "backup.zip"
    assert backup_mod.run_backup(Namespace(output=str(out_zip), keep=0)) is True

    with ZipFile(out_zip) as zf:
        assert "logs/process-results/proc_1.json" not in zf.namelist()
        assert "config.yaml" in zf.namelist()
    assert "could not be added" not in capsys.readouterr().out


def test_run_backup_still_fails_on_a_real_read_error(hermes_home, tmp_path, monkeypatch, capsys):
    """Vanished files are skipped, but an unreadable file (permissions) must keep failing the
    run with exit status 1 semantics: `run_backup` returns False."""
    from hermes_cli import backup as backup_mod

    unreadable = hermes_home / "logs" / "locked.json"
    unreadable.parent.mkdir(parents=True, exist_ok=True)
    unreadable.write_text("{}")
    unreadable.chmod(0)
    try:
        unreadable.read_text()
        pytest.skip("running as root: permission bits do not block reads")
    except PermissionError:
        pass

    out_zip = tmp_path / "backup.zip"
    assert backup_mod.run_backup(Namespace(output=str(out_zip), keep=0)) is False
    assert "could not be added" in capsys.readouterr().out


def test_run_backup_skips_sqlite_db_deleted_between_scan_and_archive(hermes_home, tmp_path, monkeypatch):
    """A ``*.db`` that vanishes mid-run must not surface as "SQLite safe copy failed" (which
    fails the CLI run and aborts the automatic backup)."""
    from hermes_cli import backup as backup_mod

    victim = hermes_home / "cron" / "scratch.db"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_bytes(b"SQLite format 3\x00")
    _delete_after_scan(monkeypatch, backup_mod, victim)

    out_zip = tmp_path / "backup.zip"
    assert backup_mod.run_backup(Namespace(output=str(out_zip), keep=0)) is True

    with ZipFile(out_zip) as zf:
        assert "cron/scratch.db" not in zf.namelist()


def test_run_backup_prunes_despite_vanished_files(hermes_home, tmp_path, monkeypatch):
    """The issue's operational pain: nightly timers kept exit-1 runs that skip ``--keep``,
    so old archives were never pruned. A run whose only casualty is a vanished file is
    complete and must prune."""
    from hermes_cli import backup as backup_mod

    victim = hermes_home / "cron" / "admissions" / "adm-1.json"
    victim.parent.mkdir(parents=True)
    victim.write_text("{}")
    _delete_after_scan(monkeypatch, backup_mod, victim)
    for i in range(3):
        (tmp_path / f"hermes-backup-2026-01-0{i + 1}-000000.zip").write_bytes(b"old")

    assert backup_mod.run_backup(Namespace(output=None, keep=2)) is True
    kept = sorted(p.name for p in tmp_path.glob("hermes-backup-*.zip"))
    assert len(kept) == 2 and not (tmp_path / "hermes-backup-2026-01-01-000000.zip").exists()
