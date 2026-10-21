"""Inbound text-batch buffering shared by every batching adapter (split from
:mod:`gateway.platforms.base`; base imports this module, never the reverse).

Chat clients split one long message into several inbound chunks; ``_enqueue_text_event``
merges chunks per session key and ``_flush_text_batch`` dispatches after a quiet period
(longer when the last chunk sits near the platform's split point, i.e. a continuation is
almost certain). Adapters set the delay attrs and ``_SPLIT_THRESHOLD``;
``_text_batch_delay_for`` / ``_pop_text_batch`` / ``_dispatch_text_batch`` are the
override seams for platform-specific policy.
"""

import asyncio
import logging
import math
from typing import Optional

from gateway.platforms.event import MessageEvent

logger = logging.getLogger(__name__)


def _record_batched_parts(pending: MessageEvent, incoming: MessageEvent) -> None:
    """Append each constituent's ``{message_id, text, timestamp}`` to the pending batch.

    The merged event keeps the opening message's id, so journaling hooks see one record
    for N messages and an edit of message #2..#N arrives with an id nobody recorded
    (#142499). Recording each part before the texts are joined preserves the per-message
    boundaries; a single-message batch never sets the key.
    """
    parts = pending.metadata.setdefault("batched_parts", [])
    if not parts:  # first merge: the pending event itself is the opening constituent
        parts.append({"message_id": pending.message_id, "text": pending.text,
                      "timestamp": pending.timestamp})
    parts.append({"message_id": incoming.message_id, "text": incoming.text,
                  "timestamp": incoming.timestamp})


class TextBatchMixin:
    """Batch merge/flush cycle mixed into ``BasePlatformAdapter``; all state stays on the
    adapter (``_pending_text_batches`` / ``_pending_text_batch_tasks`` / delay attrs)."""

    _SPLIT_THRESHOLD: int = 4000
    _text_batch_delay_seconds: float = 0.0
    _text_batch_split_delay_seconds: float = 0.0
    # Shared cadence for adapters that batch: a quiet period long enough to merge a client-side
    # split (Telegram's measured envelope), short enough that a single short message is not
    # visibly delayed (#44883). Ceilings bound a misconfigured value fed to asyncio.sleep().
    _TEXT_BATCH_DEFAULT_DELAY_S: float = 0.3
    _TEXT_BATCH_MAX_DELAY_S: float = 2.0
    _TEXT_BATCH_DEFAULT_SPLIT_DELAY_S: float = 1.0
    _TEXT_BATCH_MAX_SPLIT_DELAY_S: float = 4.0

    def _coerce_float_extra(self, key: str, default: float, *, min_value: float = 0.0, max_value: Optional[float] = None) -> float:
        """Float from ``config.extra``; NaN/Inf/negative/unparseable → ``default``; clamped to ``[min_value, max_value]``."""
        extra = getattr(self.config, "extra", None) or {}
        try:  # float(None) → TypeError → default
            parsed = float(extra.get(key))
        except (TypeError, ValueError):
            parsed = float(default)
        if not math.isfinite(parsed) or parsed < 0:
            parsed = float(default)
        parsed = max(parsed, min_value)
        if max_value is not None and parsed > max_value:
            logger.warning("%s=%s exceeds the %s ceiling; clamped", key, parsed, max_value)
            parsed = max_value
        return parsed

    def _configure_text_batch_delays(self) -> None:
        """Read ``text_batch_delay_seconds`` / ``text_batch_split_delay_seconds`` from ``config.extra`` at the shared cadence."""
        self._text_batch_delay_seconds = self._coerce_float_extra(
            "text_batch_delay_seconds", self._TEXT_BATCH_DEFAULT_DELAY_S, max_value=self._TEXT_BATCH_MAX_DELAY_S)
        self._text_batch_split_delay_seconds = self._coerce_float_extra(
            "text_batch_split_delay_seconds", self._TEXT_BATCH_DEFAULT_SPLIT_DELAY_S,
            min_value=self._text_batch_delay_seconds, max_value=self._TEXT_BATCH_MAX_SPLIT_DELAY_S)

    def _text_batch_key(self, event: MessageEvent) -> str:
        """Session-scoped key for text batching (subclasses may override)."""
        return self._event_session_key(event)

    def _enqueue_text_event(self, event: MessageEvent) -> None:
        """Buffer a text event (merging into a pending one) and restart the flush timer."""
        if self._drop_unresolved(event):
            return
        from gateway.platforms.base import _append_text  # facade late-import (no module cycle)
        key = self._text_batch_key(event)
        existing = self._pending_text_batches.get(key)
        if existing is None:
            existing = self._pending_text_batches[key] = event
        else:
            _record_batched_parts(existing, event)
            if event.text:
                existing.text = _append_text(existing.text, event.text)
            if event.media_urls:
                existing.media_urls.extend(event.media_urls)
                existing.media_types.extend(event.media_types)
            existing.absorb_reply_expected(event)
        existing._last_chunk_len = len(event.text or "")  # type: ignore[attr-defined]
        prior_task = self._pending_text_batch_tasks.get(key)
        if prior_task and not prior_task.done():
            prior_task.cancel()
        self._pending_text_batch_tasks[key] = asyncio.create_task(self._flush_text_batch(key))

    def _text_batch_delay_for(self, pending: Optional[MessageEvent]) -> float:
        """Quiet period before ``pending`` is dispatched; near-split chunks wait longer."""
        last_len = getattr(pending, "_last_chunk_len", 0) if pending is not None else 0
        return self._text_batch_split_delay_seconds if last_len >= self._SPLIT_THRESHOLD else self._text_batch_delay_seconds

    def _pop_text_batch(self, key: str) -> Optional[MessageEvent]:
        """Remove and return the pending batch for ``key`` (adapters with side tables override)."""
        return self._pending_text_batches.pop(key, None)

    async def _dispatch_text_batch(self, event: MessageEvent) -> None:
        """Hand a flushed batch to the pipeline (adapters with per-chat guards override)."""
        await self.handle_message(event)

    async def _flush_text_batch_now(self, key: str) -> None:
        """Dispatch the pending batch for ``key`` immediately (no quiet period)."""
        event = self._pop_text_batch(key)
        if event is not None:
            await self._dispatch_text_batch(event)

    async def _flush_text_batch(self, key: str) -> None:
        """Wait for the quiet period, then dispatch the batch for ``key``.

        Two races share this body. (1) ``_enqueue_text_event`` cancels the prior flush task
        on each new chunk; when ``Task.cancel()`` lands after ``sleep()`` already completed,
        CancelledError is delivered at the *next* await — after a superseded task would have
        popped the event, so the successor finds nothing and the message is lost. The identity
        check therefore runs synchronously between the sleep and the pop. (2) A cancel that
        lands while the dispatch is in flight would abort the agent turn (#12444), so the
        dispatch is shielded and the outer CancelledError swallowed."""
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(self._text_batch_delay_for(self._pending_text_batches.get(key)))
            owner = self._pending_text_batch_tasks.get(key)
            if owner is not None and owner is not current_task:
                return
            event = self._pop_text_batch(key)
            if event is None:
                return
            logger.info("[%s] Flushing text batch %s (%d chars)", self.name, key, len(event.text or ""))
            await asyncio.shield(self._dispatch_text_batch(event))
        except asyncio.CancelledError:
            pass
        finally:
            if self._pending_text_batch_tasks.get(key) is current_task:
                self._pending_text_batch_tasks.pop(key, None)
