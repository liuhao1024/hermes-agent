"""Regression tests for dashboard cron job profile routing.

After the cron storage migration (#32091), all cron jobs live in the shared
root-level store (~/.hermes/cron/jobs.json).  The Dashboard API
(``_call_cron_for_profile``) must read from the same store the scheduler and
CLI use, not from per-profile directories (#51032).
"""

import pytest
from fastapi import HTTPException


@pytest.fixture()
def isolated_profiles(tmp_path, monkeypatch):
    """Give profile discovery an isolated default home with one named profile.

    Also patches cron.jobs globals so the root store points at tmp_path.
    """
    from hermes_cli import profiles
    from cron import jobs as cron_jobs

    default_home = tmp_path / ".hermes"
    profiles_root = default_home / "profiles"
    worker_home = profiles_root / "worker_alpha"

    for home in (default_home, worker_home):
        (home / "cron").mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text("model: test-model\n", encoding="utf-8")

    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: default_home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: profiles_root)

    # Patch cron.jobs globals to use the test root store.
    monkeypatch.setattr(cron_jobs, "HERMES_DIR", default_home)
    monkeypatch.setattr(cron_jobs, "CRON_DIR", default_home / "cron")
    monkeypatch.setattr(cron_jobs, "JOBS_FILE", default_home / "cron" / "jobs.json")
    monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", default_home / "cron" / "output")

    return {"default": default_home, "worker_alpha": worker_home}


def test_call_cron_for_profile_uses_root_store(isolated_profiles):
    """_call_cron_for_profile reads/writes the shared root store, not a
    profile-local directory (#51032)."""
    from hermes_cli import web_server

    job = web_server._call_cron_for_profile(
        "worker_alpha",
        "create_job",
        prompt="run scheduled task",
        schedule="every 1h",
        name="worker-alpha-scan",
    )

    # The job is annotated with the requested profile name.
    assert job["profile"] == "worker_alpha"
    assert job["profile_name"] == "worker_alpha"
    assert job["hermes_home"] == str(isolated_profiles["worker_alpha"])
    assert job["is_default_profile"] is False

    # The job is persisted in the ROOT store, not the profile-local directory.
    root_jobs_file = isolated_profiles["default"] / "cron" / "jobs.json"
    profile_jobs_file = isolated_profiles["worker_alpha"] / "cron" / "jobs.json"
    assert root_jobs_file.exists(), "root store must contain the job"
    assert not profile_jobs_file.exists(), "profile-local store must NOT be used"


@pytest.mark.asyncio
async def test_list_cron_jobs_all_reads_from_root_store(isolated_profiles):
    """list_cron_jobs(profile='all') reads from the shared root store."""
    from hermes_cli import web_server

    job = web_server._call_cron_for_profile(
        "default",
        "create_job",
        prompt="default heartbeat",
        schedule="every 2h",
        name="default-heartbeat",
    )

    jobs = await web_server.list_cron_jobs(profile="all")
    by_id = {j["id"]: j for j in jobs}

    assert job["id"] in by_id
    assert by_id[job["id"]]["profile"] == "default"


@pytest.mark.asyncio
async def test_list_cron_jobs_specific_profile_uses_root_store(isolated_profiles):
    """list_cron_jobs(profile='worker_alpha') also reads from the shared root
    store — per-profile isolation no longer applies after migration (#32091)."""
    from hermes_cli import web_server

    job = web_server._call_cron_for_profile(
        "default",
        "create_job",
        prompt="shared job",
        schedule="every 1h",
        name="shared-job",
    )

    # Requesting a specific profile still returns root store jobs, annotated
    # with the requested profile name.
    jobs = await web_server.list_cron_jobs(profile="worker_alpha")
    by_id = {j["id"]: j for j in jobs}
    assert job["id"] in by_id
    assert by_id[job["id"]]["profile"] == "worker_alpha"


@pytest.mark.asyncio
async def test_cron_mutation_without_profile_finds_root_store_job(isolated_profiles):
    from hermes_cli import web_server

    job = web_server._call_cron_for_profile(
        "default",
        "create_job",
        prompt="managed job",
        schedule="every 1h",
        name="managed-job",
    )

    paused = await web_server.pause_cron_job(job["id"])
    assert paused["enabled"] is False

    all_jobs = await web_server.list_cron_jobs(profile="all")
    match = [j for j in all_jobs if j["id"] == job["id"]]
    assert match and match[0]["enabled"] is False


@pytest.mark.asyncio
async def test_update_cron_job_rejects_id_mutation(isolated_profiles):
    """Dashboard surfaces a 400 (not a 500 or silent rename) when an
    id-mutation attempt is rejected by cron/jobs.update_job."""
    from hermes_cli import web_server

    job = web_server._call_cron_for_profile(
        "default",
        "create_job",
        prompt="managed job",
        schedule="every 1h",
        name="immutable-id-job",
    )

    with pytest.raises(HTTPException) as exc:
        await web_server.update_cron_job(
            job["id"],
            web_server.CronJobUpdate(updates={"id": "../escape"}),
            profile="default",
        )

    assert exc.value.status_code == 400
    assert "id" in exc.value.detail
    all_jobs = await web_server.list_cron_jobs(profile="default")
    assert [j["id"] for j in all_jobs] == [job["id"]]


@pytest.mark.asyncio
async def test_cron_delete_removes_from_root_store(isolated_profiles):
    from hermes_cli import web_server

    job = web_server._call_cron_for_profile(
        "default",
        "create_job",
        prompt="deletable",
        schedule="every 1h",
        name="deletable-job",
    )

    deleted = await web_server.delete_cron_job(job["id"], profile="default")
    assert deleted == {"ok": True}

    remaining = await web_server.list_cron_jobs(profile="all")
    assert [j["id"] for j in remaining] == []


@pytest.mark.asyncio
async def test_cron_profile_validation_errors(isolated_profiles):
    from hermes_cli import web_server

    with pytest.raises(HTTPException) as bad_name:
        await web_server.list_cron_jobs(profile="../bad")
    assert bad_name.value.status_code == 400

    with pytest.raises(HTTPException) as missing:
        await web_server.list_cron_jobs(profile="missing_profile")
    assert missing.value.status_code == 404
