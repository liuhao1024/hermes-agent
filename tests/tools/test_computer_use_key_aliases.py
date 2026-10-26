"""``alt`` must survive key-combo parsing verbatim (#145174).

``_KEY_ALIASES`` used to rewrite ``alt`` to ``option`` — a macOS-only spelling. The cua-driver's
Linux and Windows hotkeys don't list ``option`` in their modifier tables, so they dropped it
silently: ``ctrl+alt+n`` reached the app as Ctrl+N and ``alt+f`` typed a bare ``f``. Every
platform accepts ``alt`` (macOS maps both names to the same flag), so the parser now forwards it
untouched. The blocklist in ``tool.py`` keeps its own input-side alias table and is unaffected.
"""

from unittest.mock import MagicMock

from tools.computer_use.cua_backend import CuaDriverBackend
from tools.computer_use.cua_backend_parse import _KEY_ALIASES, _parse_key_combo


class TestParseKeyComboAlt:
    def test_alt_is_forwarded_verbatim(self):
        assert _parse_key_combo("ctrl+alt+n") == ("n", ["ctrl", "alt"])
        assert _parse_key_combo("alt+f") == ("f", ["alt"])

    def test_hyphen_and_mixed_separators_forward_alt(self):
        assert _parse_key_combo("ctrl-alt-t") == ("t", ["ctrl", "alt"])
        assert _parse_key_combo("cmd+shift-alt+s") == ("s", ["cmd", "shift", "alt"])

    def test_explicit_option_still_parses_as_modifier(self):
        # macOS-only flows may spell the modifier ``option``; it stays a modifier.
        assert _parse_key_combo("ctrl+option+n") == ("n", ["ctrl", "option"])

    def test_unrelated_aliases_unchanged(self):
        assert "alt" not in _KEY_ALIASES
        assert _KEY_ALIASES["command"] == "cmd"
        assert _KEY_ALIASES["control"] == "ctrl"
        assert _parse_key_combo("command+control+x") == ("x", ["cmd", "ctrl"])


class TestKeyHotkeyWiring:
    def _backend_with_active_target(self):
        backend = CuaDriverBackend()
        backend._session = MagicMock()
        backend._session.call_tool.return_value = {
            "data": "ok",
            "images": [],
            "structuredContent": None,
            "isError": False,
        }
        backend._active_pid = 111
        backend._active_window_id = 222
        return backend

    def test_ctrl_alt_n_sends_alt_not_option(self):
        backend = self._backend_with_active_target()
        result = backend.key("ctrl+alt+n")
        assert result.ok is True
        name, args = backend._session.call_tool.call_args.args
        assert name == "hotkey"
        assert args["keys"] == ["ctrl", "alt", "n"]

    def test_alt_f_sends_alt_not_option(self):
        backend = self._backend_with_active_target()
        result = backend.key("alt+f")
        assert result.ok is True
        name, args = backend._session.call_tool.call_args.args
        assert name == "hotkey"
        assert args["keys"] == ["alt", "f"]

    def test_combo_without_modifier_uses_press_key(self):
        backend = self._backend_with_active_target()
        result = backend.key("n")
        assert result.ok is True
        name, args = backend._session.call_tool.call_args.args
        assert name == "press_key"
        assert args["key"] == "n"
