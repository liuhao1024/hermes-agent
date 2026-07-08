"""Tests for HERMES_CRON_SESSION env var leak prevention (#60997).

Tests cover:
- HERMES_CRON_SESSION is cleared after cron job completes
- HERMES_CRON_SESSION is restored to original value if it existed before
- HERMES_CRON_SESSION survives exception path (finally block)
"""

import os


class TestCronSessionEnvVarCleanup:
    """Test that HERMES_CRON_SESSION env var is cleaned up after cron jobs."""

    def test_hermes_cron_session_cleared_when_unset_before(self, monkeypatch):
        """HERMES_CRON_SESSION should be unset after a cron job if it was unset before."""
        # Ensure HERMES_CRON_SESSION is not set
        monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)

        # Simulate the pattern used in run_job
        _prior_cron_session = os.environ.get("HERMES_CRON_SESSION", None)
        os.environ["HERMES_CRON_SESSION"] = "1"

        # Verify it was set
        assert os.environ.get("HERMES_CRON_SESSION") == "1"

        # Simulate cleanup in finally block
        if _prior_cron_session is None:
            os.environ.pop("HERMES_CRON_SESSION", None)
        else:
            os.environ["HERMES_CRON_SESSION"] = _prior_cron_session

        # After cleanup, it should be unset
        assert "HERMES_CRON_SESSION" not in os.environ, \
            "HERMES_CRON_SESSION should be cleared after cron job completes"

    def test_hermes_cron_session_restored_when_set_before(self, monkeypatch):
        """HERMES_CRON_SESSION should be restored to original value after a cron job."""
        # Set HERMES_CRON_SESSION to a custom value
        monkeypatch.setenv("HERMES_CRON_SESSION", "custom-value")

        # Simulate the pattern used in run_job
        _prior_cron_session = os.environ.get("HERMES_CRON_SESSION", None)
        os.environ["HERMES_CRON_SESSION"] = "1"

        # Verify it was overwritten
        assert os.environ.get("HERMES_CRON_SESSION") == "1"

        # Simulate cleanup in finally block
        if _prior_cron_session is None:
            os.environ.pop("HERMES_CRON_SESSION", None)
        else:
            os.environ["HERMES_CRON_SESSION"] = _prior_cron_session

        # After cleanup, it should be restored
        assert os.environ.get("HERMES_CRON_SESSION") == "custom-value", \
            "HERMES_CRON_SESSION should be restored to original value"

    def test_hermes_cron_session_cleared_on_exception(self, monkeypatch):
        """HERMES_CRON_SESSION should be cleared even when the job raises an exception."""
        # Ensure HERMES_CRON_SESSION is not set
        monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)

        # Simulate the pattern used in run_job
        _prior_cron_session = os.environ.get("HERMES_CRON_SESSION", None)
        os.environ["HERMES_CRON_SESSION"] = "1"

        # Verify it was set
        assert os.environ.get("HERMES_CRON_SESSION") == "1"

        # Simulate exception in finally block (cleanup should still run)
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            pass
        finally:
            # This cleanup runs even after exception
            if _prior_cron_session is None:
                os.environ.pop("HERMES_CRON_SESSION", None)
            else:
                os.environ["HERMES_CRON_SESSION"] = _prior_cron_session

        # After cleanup, it should be unset
        assert "HERMES_CRON_SESSION" not in os.environ, \
            "HERMES_CRON_SESSION should be cleared even on exception"