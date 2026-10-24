"""Regression tests for #144166 — ``load_jobs()`` raises on a transient Windows
sharing violation while ``jobs.json`` is being saved.

On Windows, ``open()`` on a file another handle holds without read sharing
fails as ``PermissionError`` [Errno 13] with ``winerror`` dropped, so a read
racing ``utils.atomic_replace`` is indistinguishable from a genuine ACL denial
by shape alone. The writer's hold is brief, so the reader now retries
``PermissionError`` a bounded number of times (the same budget as the
auth-store reader, #100737) before re-raising; every other ``OSError`` is
never retried and still fails loud.
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest


@pytest.fixture
def hermes_env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "cron").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    import importlib
    import hermes_constants
    import cron.jobs

    importlib.reload(hermes_constants)
    importlib.reload(cron.jobs)
    return home


@pytest.fixture
def fast_retries(monkeypatch):
    # Keep the real delays in production; tests only care about retry counts.
    import cron.jobs

    # raising=False: absent on an unpatched tree, where the tests below then fail on
    # behavior (no retry) instead of erroring in the fixture.
    monkeypatch.setattr(
        cron.jobs, "_JOBS_READ_RETRY_DELAYS", (0.0, 0.0, 0.0), raising=False
    )


def _write_jobs(home, jobs):
    jobs_file = home / "cron" / "jobs.json"
    jobs_file.write_text('{"jobs": ' + jobs + "}", encoding="utf-8")
    return jobs_file


def _flaky_read_text(failures, exc):
    real_read = Path.read_text
    calls = {"n": 0}

    def _flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise exc
        return real_read(self, *args, **kwargs)

    return _flaky, calls


def test_transient_permission_error_is_retried_and_recovers(
    hermes_env, fast_retries, monkeypatch
):
    """A Windows file-lock collision is transient; the read must recover."""
    from cron.jobs import load_jobs

    _write_jobs(hermes_env, '[{"id": "j1", "name": "watchdog"}]')
    _flaky, calls = _flaky_read_text(
        2, PermissionError(errno.EACCES, "Permission denied")
    )
    monkeypatch.setattr(Path, "read_text", _flaky)

    jobs = load_jobs()

    assert [j["id"] for j in jobs] == ["j1"]
    assert calls["n"] == 3


def test_permanent_permission_error_still_raises(hermes_env, fast_retries, monkeypatch):
    """Retries exhausted -> the fail-loud path is unchanged, so a genuine denial surfaces."""
    from cron.jobs import load_jobs

    _write_jobs(hermes_env, '[{"id": "j1"}]')
    _flaky, calls = _flaky_read_text(
        99, PermissionError(errno.EACCES, "Permission denied")
    )
    monkeypatch.setattr(Path, "read_text", _flaky)

    with pytest.raises(RuntimeError, match="Failed to read cron database"):
        load_jobs()

    assert calls["n"] == 4


def test_other_oserror_is_not_retried(hermes_env, fast_retries, monkeypatch):
    """EMFILE/EIO-style failures get no retry: retrying cannot un-exhaust an fd pool."""
    from cron.jobs import load_jobs

    _write_jobs(hermes_env, '[{"id": "j1"}]')
    _flaky, calls = _flaky_read_text(99, OSError(errno.EMFILE, "Too many open files"))
    monkeypatch.setattr(Path, "read_text", _flaky)

    with pytest.raises(RuntimeError, match="Failed to read cron database"):
        load_jobs()

    assert calls["n"] == 1
