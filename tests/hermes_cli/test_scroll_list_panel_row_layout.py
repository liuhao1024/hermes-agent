"""Invariants for ``CLITuiMixin._render_scroll_list_panel`` (the ``/model`` picker's model
stage and the command palette) with labels long enough to fill the panel body.

The prefix (cursor or indent) must never be charged against the label's wrap budget: a 54-char
model id such as MLX Core's ``peculiar-ragdoll/Cyber-Tiel-Coder-35B-A3B-MLX-oQ4e-MTP`` used to
strand ``❯`` on a row of its own when selected, and lose its indent when not.
"""
from cli import _panel_box_width
from hermes_cli.cli_tui_mixin import _PANEL_RESERVED_BELOW, CLITuiMixin

LONG_LABEL = "peculiar-ragdoll/Cyber-Tiel-Coder-35B-A3B-MLX-oQ4e-MTP"
PICKER_TITLE = "⚙ Model Picker — MLX Core (LAN)"
HINT = "Select a model (7 available) — type to filter"


class _Host(CLITuiMixin):
    """Minimal host: the renderer only touches ``state`` + module helpers."""


def _label_rows(fragments, hint):
    text = "".join(t for _style, t in fragments)
    rows = [line[2:-2] for line in text.split("\n") if line.startswith("│ ") and line.endswith(" │")]
    return [r for r in rows if r.strip() and not r.startswith(hint)]


def _render(labels, selected, *, indent="  ", min_width=46, max_width=84, title=PICKER_TITLE, hint=HINT):
    frags = _Host()._render_scroll_list_panel(
        {"selected": selected}, title, hint, labels, min_width=min_width, max_width=max_width, indent=indent)
    return _label_rows(frags, hint)


def test_long_model_id_stays_on_the_cursor_row_and_keeps_its_indent():
    labels = ["root4k/Huihui-Qwen3.6-35B-A3B-abliterated-oQ4e-mtp", LONG_LABEL, "← Back", "Cancel"]
    body = _panel_box_width(PICKER_TITLE, [HINT] + labels, min_width=46, max_width=84) - 2

    selected = [r.rstrip() for r in _render(labels, 1)]
    assert selected[1] == f"❯ {LONG_LABEL}", selected  # cursor and full label on ONE row
    unselected = [r.rstrip() for r in _render(labels, 0)]
    assert unselected[1] == f"  {LONG_LABEL}", unselected  # indent kept, aligned with neighbours
    assert unselected[0] == f"❯ {labels[0]}"
    for rows in (selected, unselected):
        assert len(rows) == len(labels)
        assert all(len(r) <= body for r in rows), rows


def test_palette_rows_align_on_the_cursor_cell_and_wrap_with_its_indent():
    hint = "Type to filter 3 commands — ↑/↓ then Enter inserts, Esc cancels"
    long_label = "/very-long-command-name  —  " + "x" * 60
    labels = ["/model  —  Switch the active model", long_label, "/help  —  Help"]
    rows = [r.rstrip() for r in _render(labels, 1, indent="    ", min_width=50, max_width=90,
                                        title="⚙ Command Palette", hint=hint)]
    assert rows[0] == f"  {labels[0]}"  # unselected: two-column lead, same column as the cursor row
    assert rows[1].startswith("❯ /very-long-command-name")
    assert rows[2] == "    " + "x" * 60  # continuation row carries the palette's 4-space indent
    assert rows[3] == f"  {labels[2]}"

    token = "/" + "y" * 69  # one unbreakable token that fills the body: cursor must stay on its row
    rows = [r.rstrip() for r in _render([token, "/help  —  Help"], 0, indent="    ", min_width=50,
                                        max_width=90, title="⚙ Command Palette", hint=hint)]
    assert rows == [f"❯ {token}", "  /help  —  Help"], rows


def _pin_term_rows(monkeypatch, rows):
    """Force the renderer's fallback terminal size: importing ``cli`` installs a
    DummyApplication, so ``get_app().output.get_size()`` would otherwise win with the
    host process's rows instead of the pinned ones."""
    monkeypatch.setattr("hermes_cli.cli_tui_mixin._term_rows", lambda: rows)

    def _no_app():
        raise RuntimeError("no app in test")

    monkeypatch.setattr("prompt_toolkit.application.get_app", _no_app)


def test_wrapped_labels_keep_the_panel_inside_the_terminal_row_budget(monkeypatch):
    """:issue:`143140` — the viewport budget counts entries, but a wrapped label renders two
    rows, so a palette full of long "command — description" rows grew the panel past the rows
    the terminal left for it and the input area overlapped the bottom items."""
    _pin_term_rows(monkeypatch, 24)
    labels = [f"/cmd-{i:02d}  —  " + "x" * 100 for i in range(12)]  # every entry wraps
    frags = _Host()._render_scroll_list_panel(
        {"selected": 0}, "⚙ Command Palette", "Type to filter 12 commands", labels,
        min_width=50, max_width=90, indent="    ")
    text = "".join(t for _style, t in frags)
    panel_height = len(text.splitlines())
    assert panel_height <= 24 - _PANEL_RESERVED_BELOW  # the input area keeps its rows


def test_tail_selection_survives_the_wrapped_row_window(monkeypatch):
    _pin_term_rows(monkeypatch, 24)
    labels = [f"/cmd-{i:02d}  —  " + "x" * 100 for i in range(12)]
    frags = _Host()._render_scroll_list_panel(
        {"selected": 11, "_scroll_offset": 0}, "⚙ Command Palette", "Type to filter 12 commands",
        labels, min_width=50, max_width=90, indent="    ")
    text = "".join(t for _style, t in frags)
    assert any("❯ /cmd-11" in line for line in text.splitlines())
