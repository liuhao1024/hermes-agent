"""A failed cron run must leave a diagnosable trail (#104538).

Two halves of the same gap:
  - the output doc's ``## Error`` section carried only the one-line ``Type: message``
    (traceback went to the gateway log, not the file the operator reads);
  - ``_format_job`` (``cronjob(action='list')``) surfaced every last_*_error field
    except the run-side ``last_error``, so a failed run rendered as
    ``last_status="error"`` with all error fields null.

The one-line error string stays byte-identical: ``last_error`` and the delivery
failure summarizers consume it (e.g. first-line classification).
"""

from types import SimpleNamespace

import cron.scheduler as scheduler


def _run_failing_job(monkeypatch) -> tuple[bool, str, str, object]:
    monkeypatch.setattr(scheduler, "_prepare_job_prompt", lambda *a: (None, "do the sweep"))
    monkeypatch.setattr(scheduler, "_reload_dotenv_and_publish_delivery_target", lambda job: None)
    monkeypatch.setattr(
        scheduler, "_load_cron_job_config",
        lambda job, job_id, job_name: SimpleNamespace(cfg={}, model="test-model"))
    monkeypatch.setattr(
        scheduler, "_resolve_cron_agent_setup",
        lambda job, job_id, job_name, jc: SimpleNamespace(blocked=None, model="test-model"))
    monkeypatch.setattr(scheduler, "_open_cron_session_db", lambda job: None)
    monkeypatch.setattr(
        scheduler, "_construct_cron_agent", lambda *a, **k: SimpleNamespace(close=lambda: None))

    def _raise(*args, **kwargs):
        raise RuntimeError("Connection error.")

    monkeypatch.setattr(scheduler, "_run_agent_with_watchdog", _raise)
    monkeypatch.setattr(scheduler, "_write_usage_audit", lambda payload: None)

    job = {"id": "job1", "name": "hourly-sweep", "prompt": "do the sweep", "schedule": "every 1h"}
    return scheduler.run_job(job)


class TestFailedRunOutputCarriesTraceback:
    def test_error_section_includes_the_traceback(self, monkeypatch):
        success, output, final_response, error = _run_failing_job(monkeypatch)

        assert success is False
        assert "## Error" in output
        assert "Traceback (most recent call last):" in output
        assert "_raise" in output  # the raising frame, not just the exception line

    def test_returned_error_stays_the_one_line_contract(self, monkeypatch):
        # last_error and the delivery summarizers classify on this string; the
        # traceback belongs to the output doc only.
        success, output, final_response, error = _run_failing_job(monkeypatch)

        assert error == "RuntimeError: Connection error."
        assert "\n" not in error


class TestFormatJobExposesLastError:
    def test_list_output_carries_the_run_error(self):
        from tools.cronjob_job_args import _format_job

        job = {
            "id": "job1", "prompt": "do the sweep", "schedule": "every 1h",
            "last_status": "error", "last_error": "RuntimeError: Connection error.",
        }
        out = _format_job(job)

        assert out["last_status"] == "error"
        assert out["last_error"] == "RuntimeError: Connection error."

    def test_list_output_keeps_error_fields_null_on_healthy_runs(self):
        from tools.cronjob_job_args import _format_job

        job = {"id": "job1", "prompt": "do the sweep", "schedule": "every 1h"}
        out = _format_job(job)

        assert out["last_error"] is None
