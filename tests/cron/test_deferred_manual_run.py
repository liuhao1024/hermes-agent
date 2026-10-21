"""A `hermes cron run` landing while the job's previous run is still in flight must survive
that run's completion and fire on the very next tick (#142659) — not be dropped along with the
manual markers and buried until the next natural occurrence."""

from datetime import datetime, timedelta, UTC

import pytest

from cron.jobs import (
    compute_next_run,
    create_job,
    get_due_jobs,
    get_job,
    mark_job_run,
)


@pytest.fixture()
def tmp_cron_dir(tmp_path, monkeypatch):
    """Redirect cron storage to a temp directory."""
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    return tmp_path


class TestDeferredManualRun:
    def _frozen_clock(self, monkeypatch, start):
        from cron import jobs as jobs_mod

        current = {"t": start}
        monkeypatch.setattr(jobs_mod, "_hermes_now", lambda: current["t"])
        return current

    def _claim_inflight_run(self, job_id):
        from cron.jobs import claim_job_for_fire

        claim = claim_job_for_fire(job_id, return_job=True)
        assert isinstance(claim, dict), (
            "scheduled fire must win the claim to start the run"
        )
        return claim

    def test_inflight_request_survives_completion_and_fires_next_tick(
        self, tmp_cron_dir, monkeypatch
    ):
        from cron.jobs import claim_job_for_fire, trigger_job

        t0 = datetime(2026, 10, 21, 12, 29, 11, tzinfo=UTC)
        clock = self._frozen_clock(monkeypatch, t0)
        job = create_job(prompt="Report", schedule="every 15m")
        self._claim_inflight_run(job["id"])
        requested_at = (t0 + timedelta(minutes=4, seconds=13)).isoformat()  # 12:33:24
        clock["t"] = t0 + timedelta(minutes=4, seconds=13)
        assert (
            trigger_job(job["id"], extra_prompt="run now")["next_run_at"]
            == requested_at
        )

        clock["t"] = t0 + timedelta(
            minutes=19, seconds=32
        )  # 12:48:43, the run finishes
        assert mark_job_run(job["id"], success=True) is True
        deferred = get_job(job["id"])
        assert deferred["manual_run_at"] == requested_at, (
            "the request arrived mid-run; the completing run must not consume it"
        )
        assert deferred["manual_run_prompt"] == "run now"
        assert deferred["next_run_at"] == requested_at, (
            "the deferred run must fire on the next tick, not at finish + interval"
        )
        assert deferred["state"] == "scheduled"
        assert job["id"] in {j["id"] for j in get_due_jobs()}

        # The deferred fire lands as a manual run and consumes the single-fire marker.
        clock["t"] = t0 + timedelta(minutes=20)
        claimed = claim_job_for_fire(job["id"], return_job=True)
        assert isinstance(claimed, dict)
        fired = get_job(job["id"])
        assert (
            fired["next_run_at"] != requested_at
        )  # the claim advanced the schedule again
        clock["t"] = t0 + timedelta(minutes=35)
        assert mark_job_run(job["id"], success=True) is True
        after = get_job(job["id"])
        assert "manual_run_at" not in after
        assert "manual_run_prompt" not in after
        assert after["next_run_at"] == compute_next_run(
            fired["schedule"], clock["t"].isoformat()
        )

    def test_idle_request_stays_single_fire(self, tmp_cron_dir, monkeypatch):
        from cron.jobs import claim_job_for_fire, trigger_job

        t0 = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)
        clock = self._frozen_clock(monkeypatch, t0)
        job = create_job(prompt="Report", schedule="every 15m")
        assert trigger_job(job["id"])["next_run_at"] == t0.isoformat()

        clock["t"] = t0 + timedelta(seconds=5)
        assert isinstance(claim_job_for_fire(job["id"], return_job=True), dict)
        clock["t"] = t0 + timedelta(minutes=20)
        assert mark_job_run(job["id"], success=True) is True
        after = get_job(job["id"])
        assert "manual_run_at" not in after, (
            "the run dispatched AFTER the request consumed it — no deferred second run"
        )
        assert "manual_run_prompt" not in after
        assert after["next_run_at"] is not None

    def test_request_before_heartbeat_refresh_survives(self, tmp_cron_dir, monkeypatch):
        """The dispatch instant, not the last lease refresh, decides: a heartbeat rewrote
        ``fire_claim.at`` past the request, and only the immutable ``since`` keeps the
        request classified as mid-run."""
        from cron.jobs import claim_job_for_fire, heartbeat_fire_claim, trigger_job

        t0 = datetime(2026, 10, 21, 12, 29, 0, tzinfo=UTC)
        clock = self._frozen_clock(monkeypatch, t0)
        job = create_job(prompt="Report", schedule="every 15m")
        claim = self._claim_inflight_run(job["id"])
        requested_at = (t0 + timedelta(minutes=4)).isoformat()
        clock["t"] = t0 + timedelta(minutes=4)
        trigger_job(job["id"])
        clock["t"] = t0 + timedelta(minutes=11)
        assert (
            heartbeat_fire_claim(job["id"], expected_owner=claim["fire_claim"]["by"])
            is True
        )

        clock["t"] = t0 + timedelta(minutes=19)
        mark_job_run(job["id"], success=True)
        assert get_job(job["id"])["manual_run_at"] == requested_at

    def test_deferred_request_outranks_quota_park(self, tmp_cron_dir, monkeypatch):
        from cron.jobs import trigger_job

        t0 = datetime(2026, 10, 21, 12, 29, 0, tzinfo=UTC)
        clock = self._frozen_clock(monkeypatch, t0)
        job = create_job(prompt="Report", schedule="every 15m")
        self._claim_inflight_run(job["id"])
        requested_at = (t0 + timedelta(minutes=4)).isoformat()
        clock["t"] = t0 + timedelta(minutes=4)
        trigger_job(job["id"])

        clock["t"] = t0 + timedelta(minutes=19)
        mark_job_run(job["id"], success=False, error="quota", quota_hold_seconds=3600)
        after = get_job(job["id"])
        assert after["next_run_at"] == requested_at, (
            "parking past the closed window would drop the request's manual identity and prompt"
        )

    def test_oneshot_completion_does_not_pin_deferred_request(
        self, tmp_cron_dir, monkeypatch
    ):
        """A one-shot must still complete terminally: a past-due pinned marker would trip the
        120s grace retire gate on the next tick instead of firing."""
        from cron.jobs import trigger_job

        t0 = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)
        clock = self._frozen_clock(monkeypatch, t0)
        job = create_job(prompt="Once", schedule="in 30m", repeat=1)
        self._claim_inflight_run(job["id"])
        clock["t"] = t0 + timedelta(minutes=4)
        trigger_job(job["id"])

        clock["t"] = t0 + timedelta(minutes=19)
        mark_job_run(job["id"], success=True)
        after = get_job(job["id"])
        assert after["state"] == "completed"
        assert "manual_run_at" not in after
        assert after["next_run_at"] is None

    def test_terminal_repeat_clears_deferred_request(self, tmp_cron_dir, monkeypatch):
        from cron.jobs import trigger_job

        t0 = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)
        clock = self._frozen_clock(monkeypatch, t0)
        job = create_job(prompt="Report", schedule="every 15m", repeat=1)
        self._claim_inflight_run(job["id"])
        clock["t"] = t0 + timedelta(minutes=4)
        trigger_job(job["id"])

        clock["t"] = t0 + timedelta(minutes=19)
        mark_job_run(job["id"], success=True)
        after = get_job(job["id"])
        assert after["state"] == "completed", "repeat limit reached — terminal"
        assert "manual_run_at" not in after, "a terminal record cannot honor a run-now"
