"""Full-screen vs desktop-shell capture routing (#60081, wallpaper-only bug).

`capture(app='screen')` previously resolved to the OS shell/desktop window
(Progman/WorkerW on Windows) via list_windows — the wallpaper + icons layer —
so "screenshot my screen" always showed a bare desktop no matter what was
actually displayed. cua-driver's `get_desktop_state` does a real composited
full-screen grab; the `screen`/`fullscreen`/`all` sentinels now route there,
while `desktop` keeps the shell-window lane (with clickable elements).

Salvaged from @2ndNatureAI's PR #60081 (enumeration-hang bypass) and extended
with the sentinel split + no-elements guidance note.
"""

from __future__ import annotations

import base64
import io
import sys
from typing import Any, Dict, List, Optional

import pytest

# 8×8 transparent PNG — decodes cleanly for dimension sniffing.
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAADUlEQVR4nG"
    "NgGAUgAAABCAABgukLHQAAAABJRU5ErkJggg=="
)


def _png_b64_of_size(width: int, height: int) -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class _FakeSession:
    """Records tool calls; serves get_config / set_config / get_desktop_state
    and (for the desktop-shell path) list_windows / screenshot."""

    def __init__(
        self,
        windows: Optional[list[dict[str, Any]]] = None,
        desktop_image: Optional[str] = _PNG_B64,
        capture_scope: str = "window",
    ):
        self.calls: list[tuple] = []
        self._windows = windows or []
        self._desktop_image = desktop_image
        self._scope = capture_scope
        self.capabilities_discovered = True

    def _has_tool(self, name: str) -> bool:
        return name in {"get_desktop_state", "screenshot", "list_windows"}

    def call_tool(self, name: str, args: dict[str, Any], timeout: float = 30.0):
        self.calls.append((name, dict(args or {})))
        if name == "get_config":
            return {
                "data": "",
                "images": [],
                "structuredContent": {"capture_scope": self._scope},
                "isError": False,
            }
        if name == "set_config":
            self._scope = args["value"]
            return {"data": "ok", "images": [], "structuredContent": None,
                    "isError": False}
        if name == "get_desktop_state":
            images = [self._desktop_image] if self._desktop_image else []
            return {
                "data": "desktop state",
                "images": images,
                "image_mime_types": ["image/png"] if images else [],
                "structuredContent": {"screen_width": 8, "screen_height": 8},
                "isError": False,
            }
        if name == "list_windows":
            return {
                "data": "",
                "images": [],
                "structuredContent": {"windows": self._windows},
                "isError": False,
            }
        if name == "screenshot":
            return {
                "data": "",
                "images": [_PNG_B64],
                "image_mime_types": ["image/png"],
                "structuredContent": None,
                "isError": False,
            }
        raise AssertionError(f"unexpected tool call: {name}")

    def called(self, name: str) -> list[dict[str, Any]]:
        return [a for (n, a) in self.calls if n == name]


def _make_backend(session: _FakeSession):
    from tools.computer_use.cua_backend import CuaDriverBackend

    backend = CuaDriverBackend()
    backend._session = session
    backend._session_id = "test-session"
    return backend


class TestFullScreenLane:
    @pytest.mark.parametrize("sentinel", ["screen", "Screen", "fullscreen",
                                          "full screen", "all"])
    def test_screen_sentinels_route_to_get_desktop_state(self, sentinel):
        session = _FakeSession()
        backend = _make_backend(session)

        cap = backend.capture(mode="som", app=sentinel)

        assert session.called("get_desktop_state"), (
            f"app={sentinel!r} must use the composited desktop lane"
        )
        assert not session.called("list_windows"), (
            "full-screen capture must not enumerate windows (enumeration "
            "can hang on Windows — trycua/cua#2110)"
        )
        assert cap.png_b64 == _PNG_B64
        assert cap.elements == []
        assert cap.app == "screen"


    def test_capture_scope_switched_and_restored(self):
        session = _FakeSession(capture_scope="window")
        backend = _make_backend(session)

        backend.capture(mode="vision", app="screen")

        set_calls = session.called("set_config")
        assert {"key": "capture_scope", "value": "desktop",
                "session": "test-session"} in set_calls
        assert {"key": "capture_scope", "value": "window",
                "session": "test-session"} in set_calls
        assert session._scope == "window", "prior scope must be restored"

    def test_scope_untouched_when_already_desktop(self):
        session = _FakeSession(capture_scope="desktop")
        backend = _make_backend(session)

        backend.capture(mode="vision", app="screen")

        assert not session.called("set_config")

    def test_imageless_desktop_state_fails_closed_with_guidance(self):
        session = _FakeSession(desktop_image=None)
        backend = _make_backend(session)

        cap = backend.capture(mode="vision", app="screen")

        assert cap.png_b64 is None
        assert cap.window_title

    def test_dimensions_come_from_decoded_image(self):
        session = _FakeSession()
        backend = _make_backend(session)

        cap = backend.capture(mode="vision", app="screen")

        # The 8×8 PNG's real dimensions win over structuredContent.
        assert (cap.width, cap.height) == (8, 8)
        assert cap.png_bytes_len == len(base64.b64decode(_PNG_B64))

    def test_exact_pid_window_target_bypasses_full_screen_lane(self):
        session = _FakeSession()
        backend = _make_backend(session)

        backend.capture(mode="vision", app="screen", pid=123, window_id=456)

        assert not session.called("get_desktop_state"), (
            "an exact pid/window target must win over the app sentinel"
        )


class TestFullScreenMaxImageDimension:
    """#145033: ``max_image_dimension`` is pushed to the driver, which clamps its WINDOW
    lanes only — ``get_desktop_state`` comes back at native resolution, so a 5K display
    yields a multi-MB base64 payload the provider rejects (>5 MB image cap)."""

    def test_oversized_full_screen_capture_is_downscaled_to_cap(self, monkeypatch):
        monkeypatch.setattr("tools.computer_use.cua_backend._computer_use_max_image_dimension",
                            lambda: 100)
        big = _png_b64_of_size(448, 252)  # 4.5x the cap on the long edge
        session = _FakeSession(desktop_image=big)
        backend = _make_backend(session)

        cap = backend.capture(mode="vision", app="screen")

        assert (cap.width, cap.height) == (100, 56), "longest edge must land on the cap, aspect kept"
        assert cap.png_bytes_len < len(base64.b64decode(big)), "downscale must shrink the payload"
        assert cap.png_b64, "downscaled capture must still carry pixels"

    def test_within_cap_capture_is_returned_byte_for_byte(self, monkeypatch):
        monkeypatch.setattr("tools.computer_use.cua_backend._computer_use_max_image_dimension",
                            lambda: 1456)
        session = _FakeSession()
        backend = _make_backend(session)

        cap = backend.capture(mode="vision", app="screen")

        assert cap.png_b64 == _PNG_B64, "no silent re-encode of already-compliant captures"

    def test_missing_pillow_still_returns_the_native_capture(self, monkeypatch):
        big = _png_b64_of_size(448, 252)
        monkeypatch.setattr("tools.computer_use.cua_backend._computer_use_max_image_dimension",
                            lambda: 100)
        monkeypatch.setitem(sys.modules, "PIL", None)
        session = _FakeSession(desktop_image=big)
        backend = _make_backend(session)

        cap = backend.capture(mode="vision", app="screen")

        assert cap.png_b64 == big, "Pillow-less environments keep today's (passthrough) behavior"


class TestDesktopShellLane:
    _PROGMAN = {
        "app_name": "Progman",
        "title": "Program Manager",
        "pid": 100,
        "window_id": 1,
        "off_screen": False,
        "z_index": 0,
    }

    def test_desktop_sentinel_keeps_shell_window_lane(self):
        session = _FakeSession(windows=[self._PROGMAN])
        backend = _make_backend(session)

        cap = backend.capture(mode="vision", app="desktop")

        assert session.called("list_windows"), (
            "app='desktop' must still resolve the shell window so desktop "
            "icons stay clickable"
        )
        assert not session.called("get_desktop_state")
        assert backend._active_pid == 100
        assert cap.note == ""

    def test_desktop_sentinel_without_shell_window_fails_with_guidance(self):
        session = _FakeSession(windows=[{
            "app_name": "Notepad", "title": "Untitled", "pid": 7,
            "window_id": 9, "off_screen": False, "z_index": 1,
        }])
        backend = _make_backend(session)

        cap = backend.capture(mode="vision", app="desktop")

        assert cap.png_b64 is None
        assert cap.window_title


class TestNoteInSummary:
    def test_capture_response_appends_note_line(self, tmp_path, monkeypatch):
        import hermes_constants
        from tools.computer_use.backend import CaptureResult
        from tools.computer_use import tool as cu_tool

        monkeypatch.setattr(hermes_constants, "get_hermes_dir",
                            lambda *a, **k: tmp_path)
        monkeypatch.setattr(cu_tool, "_should_route_through_aux_vision",
                            lambda: False)

        cap = CaptureResult(
            mode="vision", width=8, height=8, png_b64=_PNG_B64,
            elements=[], app="screen",
            window_title="Full screen (composited)",
            png_bytes_len=len(base64.b64decode(_PNG_B64)),
            note="full-screen capture has no interactable elements; "
                 "call capture(app='desktop') for the desktop shell",
        )
        result = cu_tool._capture_response(cap)

        text = str(result)
        assert "full-screen capture has no interactable elements" in text
