"""A human-paced typing burst (one character per keypress, key-to-key gaps above the
rapid-input window) must not have its submit Enter eaten as a paste newline (#142927).

Windows Terminal delivers Enter and Shift+Enter as the same byte, so the only newline
keys a stock-WT user has (Ctrl+J / the submit key misused as Shift+Enter) all land in
``_tui_handle_enter``. The #10994 rapid-input window must therefore only swallow an
Enter when the recent text changes themselves carry machine-pace evidence: a
multi-character insert (whole-chunk paste) or a chain of changes closer together than
the window. Human typing is one character per change with gaps above the window — its
Enter is a real submit.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from prompt_toolkit.buffer import Buffer


def _shell():
    from cli import HermesCLI
    shell = object.__new__(HermesCLI)
    shell._tui_enter_overlay = lambda event: False
    shell._tui_multiline_shortcuts = True
    shell._attached_images = []
    shell._agent_running = False
    shell._pending_input = MagicMock()
    shell._tui_enter_inline_command = lambda *a, **k: False
    shell._inline_pastes = lambda buf: None
    shell._tui_paste_counter = 0
    shell.config = {}
    shell._tui_prev_text_len = shell._tui_prev_newline_count = 0
    shell._tui_paste_just_collapsed = shell._skip_paste_collapse = False
    shell._tui_paste_over_threshold = lambda *a, **k: False
    return shell


def _event(buf: Buffer):
    return SimpleNamespace(app=SimpleNamespace(current_buffer=buf, invalidate=lambda: None, is_running=False))


class _Clock:
    """Pinned monotonic clock so key-to-key gaps are exact."""

    def __init__(self, start: float):
        self.now = start

    def advance(self, dt: float) -> None:
        self.now += dt


def _type_char(shell, buf, ch, clock, gap):
    clock.advance(gap)
    buf.insert_text(ch)


def test_human_paced_typing_submit_enter_is_not_eaten():
    """Typing `/goal` + newline + text at human key-to-key gaps, then Enter within the
    rapid window of the last keypress: the Enter submits (queue receives the multi-line
    message); it must not become another newline."""
    shell = _shell()
    buf = Buffer()
    buf.on_text_changed += shell._tui_on_text_changed
    clock = _Clock(1000.0)
    with patch.object(time, "monotonic", lambda: clock.now):
        for ch in "/goal":
            _type_char(shell, buf, ch, clock, 0.08)      # 80 ms key-to-key, human pace
        _type_char(shell, buf, "\n", clock, 0.08)        # Ctrl+J newline, human pace
        for ch in "write me a poem.":
            _type_char(shell, buf, ch, clock, 0.08)
        # The submit Enter lands 30 ms after the final '.' — inside the window, but every
        # change before it was a single character at human gaps.
        clock.advance(0.03)
        shell._tui_handle_enter(_event(buf))
    assert shell._pending_input.put.called, "human-paced submit Enter was swallowed as a newline"
    submitted = shell._pending_input.put.call_args.args[0]
    assert submitted == "/goal\nwrite me a poem."


def test_machine_paced_char_chain_enter_still_a_newline():
    """A no-bracketed-paste paste delivered character-by-character (tmux) has machine-pace
    gaps (< the window between changes); an Enter inside that stream stays a newline
    (#10994)."""
    shell = _shell()
    buf = Buffer()
    buf.on_text_changed += shell._tui_on_text_changed
    clock = _Clock(2000.0)
    with patch.object(time, "monotonic", lambda: clock.now):
        for ch in "line one":                              # 4 ms per char — machine pace
            _type_char(shell, buf, ch, clock, 0.004)
        clock.advance(0.004)
        shell._tui_handle_enter(_event(buf))               # the paste's own newline-Enter
        for ch in "line two":
            _type_char(shell, buf, ch, clock, 0.004)
    assert buf.text == "line one\nline two", "mid-paste Enter should have become a newline"
    assert not shell._pending_input.put.called
