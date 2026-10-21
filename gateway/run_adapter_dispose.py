"""Disposal of unowned platform adapters (reconnect-watcher path).

Lives outside ``gateway/run.py`` (already over its FILE_LINES cap) but is re-exported
from there: ``run_adapters.py`` and the fd-leak regression tests import it as
``gateway.run._dispose_unused_adapter``.
"""

from __future__ import annotations

import asyncio
import logging

from gateway.platforms.base import BasePlatformAdapter

logger = logging.getLogger(__name__)

# Bound on disposing an unowned adapter's disconnect() (#142670): a disconnect wedged on a
# starved event loop (e.g. Discord's client.start() task never surfacing CancelledError)
# must not stall the reconnect watcher, because every other primary platform's reconnect
# rides on the same task. Generous on purpose: it covers voice leave + bot-task cancel +
# client.close() with headroom.
_ADAPTER_DISPOSE_TIMEOUT = 30.0


def _log_dispose_late_failure(task: asyncio.Future) -> None:
    """Surface a late failure from an abandoned dispose instead of 'exception was never retrieved'."""
    if not task.cancelled() and task.exception() is not None:
        logger.debug("Abandoned adapter dispose failed late", exc_info=task.exception())


async def _dispose_unused_adapter(adapter: BasePlatformAdapter | None) -> None:
    """Best-effort dispose for an adapter that never made it onto ``self.adapters`` (may be ``None``).
    Nothing else calls ``disconnect()`` on it, so ``__init__`` resources (e.g. SQLite fds) would leak
    until GC (not prompt for asyncio-bound objects) and exhaust the fd ulimit over a long retry loop.

    The reconnect watcher in ``GatewayRunner._platform_reconnect_watcher`` constructs a fresh adapter on
    every retry attempt. When the connect call fails — for any of the three reasons (non-retryable error,
    retryable error, exception during connect) — the adapter is dropped without ever being installed, so
    nothing else will call its ``disconnect()``. ``APIServerAdapter`` opens a SQLite ``ResponseStore`` that
    holds 2 fds — the db file and its WAL sidecar) stay open until garbage collection sweeps the unreachable
    object, which Python's cyclic GC does not do promptly for asyncio-bound objects with native handles. The
    cumulative leak is 2 fds × every retry at the 300s backoff cap ≈ 12 fds/hour, and the default 2560-fd
    ulimit is exhausted in ~12h of continuous failure, after which every open() call on the gateway raises
    ``OSError: [Errno 24] Too many open files`` and the gateway becomes a zombie (#37011).
    """
    if adapter is None:
        return
    task = asyncio.ensure_future(adapter.disconnect())
    try:
        # shield, not a bare wait_for: on timeout wait_for cancels and then awaits the inner
        # awaitable, so a disconnect() that stalls past cancellation would still hang this
        # await — and with it the reconnect watcher — forever (#142670). The shield makes the
        # bound final; the wedged task keeps running detached.
        await asyncio.wait_for(asyncio.shield(task), timeout=_ADAPTER_DISPOSE_TIMEOUT)
    except TimeoutError:
        logger.warning(
            "Adapter %r disconnect exceeded %.0fs dispose bound; abandoning it (reconnect watcher continues)",
            getattr(adapter, "name", type(adapter).__name__),
            _ADAPTER_DISPOSE_TIMEOUT,
        )
        task.add_done_callback(_log_dispose_late_failure)
    except asyncio.CancelledError:
        task.cancel()  # watcher shutdown propagates into the dispose, matching a plain await
        raise
    except Exception:
        # Half-constructed adapters may raise; must not abort the watcher (CancelledError propagates).
        logger.debug(
            "Adapter dispose raised on unowned adapter %r",
            getattr(adapter, "name", type(adapter).__name__),
            exc_info=True,
        )
