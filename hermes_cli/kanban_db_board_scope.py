"""Board-scope identity for the Kanban DB: the slug vocabulary, the context
overrides that scope a resolution (CLI ``--board`` / dashboard plugin_api) or
pin a machine flow to env-first resolution, and the helpers that read explicit
caller intent out of them.

Split out of ``hermes_cli.kanban_db`` (self-contained: stdlib only, no
``_kb`` back-reference), so ``kanban_db.<name>`` re-exports keep every
``kb.<name>`` monkeypatch target working.
"""

from __future__ import annotations

import contextlib
import re
from contextvars import ContextVar, Token
from typing import Optional


DEFAULT_BOARD = "default"
_CURRENT_BOARD_OVERRIDE: ContextVar[str | None] = ContextVar(
    "hermes_kanban_current_board_override", default=None,
)
# Machine flows that enumerate boards (gateway notifier / watcher / dispatcher
# ticks) resolve board paths env-pin-first — see pin_first_board_resolution().
_PIN_FIRST_BOARD_RESOLUTION: ContextVar[bool] = ContextVar(
    "hermes_kanban_pin_first_board_resolution", default=False,
)


@contextlib.contextmanager
def pin_first_board_resolution():
    """Resolve board paths env-pin-first for machine flows that enumerate boards.

    The gateway notifier, per-subscription cursor writes and the embedded
    dispatcher poll every board slug from ``list_boards()`` — the slug is not
    caller intent, it is an iteration variable. On a box whose environment pins
    ``HERMES_KANBAN_DB`` (the dispatcher default) every slug must map to that
    one pinned file: the notifier dedupes resolved DB paths, and per-slug
    paths read empty boards nobody writes, silently killing wake
    notifications. Explicit cross-board intent (CLI ``--board``, a model
    tool's ``board=``) is a USER property and must never run inside this
    context; with no pin set this context changes nothing.
    """
    token = _PIN_FIRST_BOARD_RESOLUTION.set(True)
    try:
        yield
    finally:
        _PIN_FIRST_BOARD_RESOLUTION.reset(token)


@contextlib.contextmanager
def scoped_current_board(slug: str):
    """Pin the active board for the current context only."""
    token: Token[str | None] = _CURRENT_BOARD_OVERRIDE.set(slug)
    try:
        yield
    finally:
        _CURRENT_BOARD_OVERRIDE.reset(token)


# Slug = directory name: strict enough to stop traversal / separators, loose
# enough for kebab-case. Display names (spaces, emoji) live in board.json.
_BOARD_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,63}$")


def _normalize_board_slug(slug: Optional[str]) -> Optional[str]:
    """Lowercase + strip a slug; validate; return ``None`` for empty."""
    s = str(slug).strip().lower() if slug is not None else ""
    if not s:
        return None
    if not _BOARD_SLUG_RE.match(s):
        raise ValueError(
            f"invalid board slug {slug!r}: must be 1-64 chars, lowercase "
            f"alphanumerics / hyphens / underscores, not starting with '-' or '_'"
        )
    return s


def _slug_or_default(board: Optional[str]) -> str:
    return _normalize_board_slug(board) or DEFAULT_BOARD


def _require_slug(slug: str) -> str:
    normed = _normalize_board_slug(slug)
    if not normed:
        raise ValueError("board slug is required")
    return normed


def _explicit_board_slug(board: Optional[str]) -> Optional[str]:
    """Explicit caller intent: a direct ``board=`` argument, else the scoped
    ``--board`` context (CLI ``hermes kanban --board``, dashboard plugin_api);
    ``None`` when the caller expressed neither."""
    if board is not None:
        return _normalize_board_slug(board)
    # A caller-scoped board (CLI `hermes kanban --board B ...`, dashboard
    # plugin_api) is explicit intent just like a direct board= argument —
    # without this a worker-pinned HERMES_KANBAN_DB silently outranks --board
    # (os-reviewer P1 on PR#107195 / t_11c4afd8).
    ctx = (_CURRENT_BOARD_OVERRIDE.get() or "").strip()
    if ctx:
        try:
            return _normalize_board_slug(ctx)
        except ValueError:
            return None
    return None
