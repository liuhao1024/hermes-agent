"""The scheduler stamps ``HERMES_CRON_JOB_ID`` on cron-script spawns (#133135).

A no-agent job that wraps ``hermes cron doctor`` used to fail forever: its own failed-run
stdout became ``last_error``, the doctor re-reported the invoking job, and the nested output
grew on every tick. The fix's spawn side is here; the doctor-side exclusion lives in
``tests/hermes_cli/test_cron_satellite_diagnostics.py``.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def hermes_env(tmp_path, monkeypatch):
    """Isolate HERMES_HOME for each test so jobs/scripts don't leak."""
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "scripts").mkdir()
    (home / "cron").mkdir()

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_CRON_JOB_ID", raising=False)

    # Reload modules that cache get_hermes_home() at import time.
    import importlib
    import hermes_constants
    importlib.reload(hermes_constants)
    import cron.jobs
    importlib.reload(cron.jobs)
    import cron.scheduler
    importlib.reload(cron.scheduler)

    return home


def test_run_job_script_stamps_invoking_job_id(hermes_env):
    """``job_id=`` reaches the script process as ``HERMES_CRON_JOB_ID`` so the ``cron doctor``
    it shells out to can exclude its own invoking job; spawns without a job id (monitor probes)
    stay unstamped."""
    from cron.scheduler_script import _run_job_script

    (hermes_env / "scripts" / "probe.sh").write_text(
        'if [ -n "$HERMES_CRON_JOB_ID" ]; then echo "stamped:$HERMES_CRON_JOB_ID"; '
        'else echo "unstamped"; fi\n', encoding="utf-8")

    ok, output = _run_job_script("probe.sh", job_id="job-123")
    assert ok is True
    assert output == "stamped:job-123"

    ok, output = _run_job_script("probe.sh")
    assert ok is True
    assert output == "unstamped"
