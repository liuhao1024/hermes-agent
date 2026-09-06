"""`/cron list` must show why an errored run failed, not just "(error)" (#104538).

delivery_failed already appends its reason (last_delivery_error); the error
status now appends the run-side reason (last_error) the same way.
"""

import hermes_cli.cli_commands_mixin as mixin
from hermes_cli.cli_commands_mixin import CLICommandsMixin


def _list_with_job(monkeypatch, capsys, job_extra: dict) -> str:
    job = {
        "job_id": "job1", "name": "hourly-sweep", "state": "scheduled",
        "schedule": "every 1h", "repeat": "infinite", "next_run_at": "2100-01-01T00:00:00Z",
        "prompt_preview": "do the sweep",
    }
    job.update(job_extra)
    monkeypatch.setattr(mixin, "_cron_api", lambda **kw: {"success": True, "jobs": [job]})
    CLICommandsMixin._cron_list(object(), "list", {"all": False})
    return capsys.readouterr().out


class TestCronListShowsLastError:
    def test_error_status_appends_the_run_error(self, monkeypatch, capsys):
        out = _list_with_job(monkeypatch, capsys, {
            "last_run_at": "2026-09-05T16:52:22Z", "last_status": "error",
            "last_error": "RuntimeError: Connection error.",
        })
        assert "error: RuntimeError: Connection error." in out

    def test_error_status_without_a_reason_stays_bare(self, monkeypatch, capsys):
        out = _list_with_job(monkeypatch, capsys, {
            "last_run_at": "2026-09-05T16:52:22Z", "last_status": "error",
        })
        assert "(error)" in out

    def test_ok_status_is_untouched(self, monkeypatch, capsys):
        out = _list_with_job(monkeypatch, capsys, {
            "last_run_at": "2026-09-05T13:49:23Z", "last_status": "ok",
            "last_error": "RuntimeError: stale from an earlier failure.",
        })
        assert "(ok)" in out
        assert "stale from an earlier failure" not in out
