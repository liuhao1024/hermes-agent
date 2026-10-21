"""#142414: a slow-but-alive owner must not lose its restart-safe observer status poll.

One ``cron.status`` answer that outlives ``DEFAULT_RPC_TIMEOUT`` used to end the observer with
``CronExecutionUnknown`` (via ``connect_gateway`` re-reporting the response timeout as a connection
failure), pausing a job the owner was still running. The observer's cron verbs now wait on
``CRON_RPC_TIMEOUT``.
"""

import asyncio
from contextlib import asynccontextmanager

from hermes_cli import gateway_client


def _connected(peer):
    @asynccontextmanager
    async def client():
        yield peer

    return client


def test_observer_cron_rpcs_carry_the_cron_budget(monkeypatch):
    """Every cron verb the restart-safe worker issues waits on ``CRON_RPC_TIMEOUT``, never on
    ``DEFAULT_RPC_TIMEOUT``: a busy owner answering past 30 s (observed 20-80 s) is slow, not gone."""
    from cron.scheduler_authority import run_canonical_job

    seen = []

    class Peer:
        async def rpc(self, method, **params):
            seen.append((method, params.pop("_timeout", None)))
            if method == "cron.submit":
                return {"session_id": "cron-fire", "admission_id": "admit"}
            if method == "cron.status":
                return {
                    "status": "terminal",
                    "result": [True, "document", "answer", None],
                }
            raise AssertionError(method)

    monkeypatch.setattr(gateway_client, "connect_gateway", _connected(Peer()))
    assert run_canonical_job({"id": "job"}, execution_id="fire") == (
        True,
        "document",
        "answer",
        None,
    )
    assert seen and {budget for _, budget in seen} == {gateway_client.CRON_RPC_TIMEOUT}


def test_slow_owner_answer_outlives_the_default_rpc_budget(monkeypatch):
    """A ``cron.status`` answer slower than the default budget must be waited out, not turned into
    ``CronExecutionUnknown`` while the owner is still running the job."""
    from cron.scheduler_authority import run_canonical_job

    monkeypatch.setattr(gateway_client, "DEFAULT_RPC_TIMEOUT", 0.05)

    class BusyOwner:
        """Answers only after a delay longer than the (patched, tiny) default budget, exactly the
        observed busy-owner shape: alive, admitted, and slow to answer each poll."""

        async def rpc(self, method, **params):
            budget = params.pop("_timeout", None) or gateway_client.DEFAULT_RPC_TIMEOUT
            # health: allow HX013 -- the busy owner's answer delay IS the behaviour under test
            await asyncio.wait_for(asyncio.sleep(0.2), budget)
            if method == "cron.submit":
                return {"session_id": "cron-fire", "admission_id": "admit"}
            if method == "cron.status":
                return {
                    "status": "terminal",
                    "result": [True, "document", "answer", None],
                }
            raise AssertionError(method)

    monkeypatch.setattr(gateway_client, "connect_gateway", _connected(BusyOwner()))
    assert run_canonical_job({"id": "job"}, execution_id="fire") == (
        True,
        "document",
        "answer",
        None,
    )
