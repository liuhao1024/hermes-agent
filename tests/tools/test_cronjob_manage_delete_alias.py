"""Regression tests for #144157 / #144279 — ``cronjob_manage`` accepts the natural
``delete`` verb instead of dying on "Unknown cron action 'delete'", and an unknown
action's error names the supported actions so the calling agent stops guessing.

Only ``remove`` existed while agents (and issue reporters) reach for ``delete``
first, so finished jobs piled up paused forever.
"""
import json

import pytest

from tools.cronjob_tools import cronjob


@pytest.fixture(autouse=True)
def _setup_cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")


def _create_job():
    created = json.loads(cronjob(action="create", prompt="Check", schedule="every 1h"))
    assert created["success"] is True
    return created["job_id"]


def test_delete_action_removes_the_job():
    job_id = _create_job()

    deleted = json.loads(cronjob(action="delete", job_id=job_id))
    assert deleted["success"] is True

    from cron.jobs import get_job
    assert get_job(job_id) is None


def test_remove_still_works_alongside_delete_alias():
    job_id = _create_job()

    removed = json.loads(cronjob(action="remove", job_id=job_id))
    assert removed["success"] is True


def test_unknown_action_error_lists_supported_actions():
    job_id = _create_job()

    error = json.loads(cronjob(action="del", job_id=job_id))
    assert error["success"] is False
    # The whole point: the caller can self-correct without re-reading the schema.
    for verb in ("create", "list", "update", "pause", "resume", "remove", "run"):
        assert verb in error["error"], error["error"]
    assert "delete" in error["error"]
