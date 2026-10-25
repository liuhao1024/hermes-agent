"""``_bounded_health_probe`` must honour the route's probe budget (issue #144360).

The probe used to run inside ``with ThreadPoolExecutor(...)``, whose ``__exit__`` calls
``shutdown(wait=True)``: after ``future.result(timeout=...)`` gave up, the ``with`` block
still joined the worker, so ``GET /api/status`` waited for the probe's own duration (the
warning fired at the budget while the response arrived much later). The teardown must not
block on a probe the route already gave up on.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from hermes_cli.web_routers import status


@pytest.fixture()
def probe_budget(monkeypatch):
    monkeypatch.setattr(status, "_GATEWAY_HEALTH_ROUTE_TIMEOUT", 0.25)


def test_hung_probe_returns_within_the_route_budget(monkeypatch, caplog, probe_budget):
    release = threading.Event()

    def hung_probe():
        release.wait(3.0)
        return True, {"gateway_state": "running"}

    monkeypatch.setattr(status, "_probe_gateway_health", hung_probe)
    with caplog.at_level(logging.WARNING, logger="hermes_cli.web_server"):
        try:
            start = time.monotonic()
            result = status._bounded_health_probe()
            elapsed = time.monotonic() - start
        finally:
            release.set()

    assert result == (False, None)
    assert elapsed < 2.0, (
        f"the probe budget must bound the request, not the probe's own duration "
        f"(took {elapsed:.3f}s)"
    )
    assert any("exceeded" in rec.getMessage() for rec in caplog.records)


def test_probe_finishing_in_budget_still_returns_its_value(monkeypatch, probe_budget):
    monkeypatch.setattr(
        status, "_probe_gateway_health", lambda: (True, {"gateway_state": "running"})
    )
    assert status._bounded_health_probe() == (True, {"gateway_state": "running"})


def test_failing_probe_still_reports_not_running(monkeypatch, probe_budget):
    def boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(status, "_probe_gateway_health", boom)
    assert status._bounded_health_probe() == (False, None)
