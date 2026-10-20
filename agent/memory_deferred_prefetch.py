"""Opt-in ownership/claiming for the manager's existing gated prefetch worker."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hermes_constants import hermes_home_key

if TYPE_CHECKING:
    from agent.memory_manager import MemoryManager
    from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)


@dataclass
class _DeferredCall:
    query: str
    session_id: str
    generation: int
    thread: threading.Thread | None = None
    waiting: bool = True
    done: bool = False
    consumed: bool = False
    value: str = ""


class _DeferredPrefetch:
    """One local worker lease per provider, independent of process-wide admission.

    All state uses the manager's late-cache lock/generation. The waiter detaches in
    finally; only actual worker completion (or failure to start) releases its fence.
    No queue: completed abandoned calls use the manager's existing single late value.
    """

    def __init__(self) -> None:
        self._active: dict[str, _DeferredCall] = {}
        self._binding: tuple[str, str] | None = None

    def prefetch(self, manager: MemoryManager, provider: MemoryProvider, query: str, session_id: str) -> str:
        binding = (hermes_home_key(), session_id)
        with manager._late_prefetch_lock:
            if binding != self._binding:
                manager._late_prefetch.clear()
                manager._late_prefetch_generation += 1
                self._binding = binding
            if manager._shutting_down:
                return ""
            generation = manager._late_prefetch_generation
            late = manager._late_prefetch.pop(provider.name, None)
            earlier = (late.value if late and session_id and session_id.strip()
                       and late.session_id == session_id else "")
            call = None
            if provider.name not in self._active:
                call = _DeferredCall(query, session_id, generation)
                self._active[provider.name] = call
        fresh = ""
        if call is not None:
            try:
                fresh = manager._prefetch_external(provider, query, session_id=session_id, deferred=call)
            except Exception as exc:
                # A fresh failure must not swallow a value already claimed for this turn.
                logger.debug("Memory provider '%s' prefetch failed (non-fatal): %s", provider.name, exc, exc_info=True)
            finally:
                with manager._late_prefetch_lock:
                    call.waiting = False
                    if call.thread is None or call.thread.ident is None:
                        call.done = True  # no worker finally exists on admission/start failure
                    self._settle_locked(manager, provider.name, call)
        parts = []
        label = "[Earlier-turn recall for a previous question; not evidence for the current topic]"
        if earlier:
            parts.append(label + "\n" + earlier)
        if fresh:
            parts.append(("[Current-question recall]\n" if earlier else "") + fresh)
        combined = "\n\n".join(parts)
        result = manager._spill_external_prefetch(
            combined, session_id=session_id, provider_name=provider.name,
        )
        if earlier and result != combined:
            result = label + "\n" + result  # origin survives even a zero-length spill preview
        with manager._late_prefetch_lock:
            return result if generation == manager._late_prefetch_generation and not manager._shutting_down else ""

    def complete(self, manager: MemoryManager, provider_name: str, call: _DeferredCall, raw: str) -> None:
        """Publish only after the real gated worker finishes, racing safely with wait cleanup."""
        value = ""
        try:
            if raw and raw.strip():
                value = manager._spill_external_prefetch(raw, session_id=call.session_id, provider_name=provider_name)
        finally:
            with manager._late_prefetch_lock:
                call.value = value
                call.done = True
                self._settle_locked(manager, provider_name, call)

    def _settle_locked(self, manager: MemoryManager, provider_name: str, call: _DeferredCall) -> None:
        if not call.done or call.waiting:
            return
        if (not call.consumed and call.value and call.session_id and call.session_id.strip()
                and call.generation == manager._late_prefetch_generation and not manager._shutting_down):
            from agent.memory_manager import _LatePrefetch
            manager._late_prefetch[provider_name] = _LatePrefetch(call.query, call.session_id, call.value)
        if self._active.get(provider_name) is call:
            del self._active[provider_name]
