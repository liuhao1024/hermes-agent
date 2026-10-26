"""HTTP JSON watcher diagnostics must not replay request/exception text.

Uses only a loopback fixture, never external endpoints or real credentials.
"""
import contextlib
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error


SCRIPT = (Path(__file__).resolve().parents[2] /
          "optional-skills/devops/watchers/scripts/watch_http_json.py")
REQUEST_MARK = "SYNTHETIC_REQUEST_QUERY"
SERVER_MARK = "SYNTHETIC_SERVER_STATUS"


def isolated_env(root):
    env = {"PATH": os.defpath, "LANG": "C.UTF-8", "TZ": "UTC",
           "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "HERMES_DISABLE_LAZY_INSTALLS": "1", "NO_PROXY": "*"}
    for key in ("HOME", "HERMES_HOME", "TMPDIR", "XDG_CACHE_HOME",
                "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                "XDG_RUNTIME_DIR", "HERMES_RUNTIME_DIR", "HERMES_MANAGED_ROOT",
                "HERMES_PM_ROOT", "WATCHER_STATE_DIR"):
        dest = root / key.lower()
        dest.mkdir()
        env[key] = str(dest)
    return env


class WatcherHTTPJSONTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="watcher-http-")
        self.addCleanup(temp.cleanup)
        self.env = isolated_env(Path(temp.name))
        self.state = Path(self.env["WATCHER_STATE_DIR"]) / "events.json"

    def cli(self, url, *args):
        return subprocess.run(
            [sys.executable, "-B", "-W", "default", str(SCRIPT),
             "--name", "events", "--url", url, "--body-field", "body", *args],
            cwd=self.env["TMPDIR"], env=self.env,
            capture_output=True, text=True, timeout=15, check=False,
        )

    def test_cli_errors_preserve_watermark_and_valid_content(self):
        items = [{"id": "old", "title": "Old"}]

        class Handler(BaseHTTPRequestHandler):
            def do_GET(handler):
                if handler.path.startswith("/bad-status"):
                    handler.wfile.write(("GARBAGE " + SERVER_MARK + "\r\n\r\n").encode())
                    return
                if handler.path.startswith("/limited"):
                    handler.send_response(429, SERVER_MARK)
                    handler.send_header("Content-Length", "0")
                    handler.end_headers()
                    return
                body = json.dumps(items).encode()
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(body)))
                handler.end_headers()
                handler.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.01})
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            baseline = self.cli(base + "/feed")
            self.assertEqual((baseline.returncode, baseline.stdout, baseline.stderr), (0, "", ""))
            before = self.state.read_bytes()
            cases = [
                (base + "/limited?sig=" + REQUEST_MARK, (), "watch_http_json: HTTP 429\n"),
                (base + "/bad-status?sig=" + REQUEST_MARK, (),
                 "watch_http_json: request error: BadStatusLine\n"),
                (base + "/feed?sig=" + REQUEST_MARK,
                 ("--header", "X-Test: a\r\nSYNTHETIC_HEADER"),
                 "watch_http_json: request error: ValueError\n"),
                ("nosuch://fixture.invalid/?sig=" + REQUEST_MARK, (),
                 "watch_http_json: network error: URLError (str)\n"),
            ]
            for url, args, diagnostic in cases:
                with self.subTest(diagnostic=diagnostic):
                    result = self.cli(url, *args)
                    self.assertEqual(self.state.read_bytes(), before)
                    self.assertEqual((result.returncode, result.stdout, result.stderr),
                                     (2, "", diagnostic))
            items.append({"id": "new", "title": "Fresh",
                          "url": "https://example.invalid/view?signature=SYNTHETIC_CONTENT",
                          "body": "fresh body"})
            fresh = self.cli(base + "/feed")
            self.assertEqual((fresh.returncode, fresh.stderr), (0, ""))
            self.assertEqual(fresh.stdout,
                             "## Fresh\nhttps://example.invalid/view?signature=SYNTHETIC_CONTENT\n\nfresh body\n")
            self.assertEqual(set(json.loads(self.state.read_text())["seen_ids"]), {"old", "new"})
            repeat = self.cli(base + "/feed")
            self.assertEqual((repeat.returncode, repeat.stdout, repeat.stderr), (0, "", ""))
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()
            self.assertFalse(thread.is_alive())
            with socket.socket() as probe:
                probe.settimeout(2)
                self.assertNotEqual(probe.connect_ex(server.server_address), 0)

    def test_named_error_routing_close_and_unrelated_propagation(self):
        # Product import occurs only after all writable homes are isolated.
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(sys, "path", list(sys.path)), \
                patch.dict(sys.modules):
            sys.modules.pop("_watermark", None)
            spec = importlib.util.spec_from_file_location("watch_http_json_test", SCRIPT)
            watcher = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(watcher)
            self.state.write_text('{"seen_ids":["keep"],"first_run":false}')
            before = self.state.read_bytes()

            class UnprintableHTTPError(urllib.error.HTTPError):
                def __str__(self):
                    raise AssertionError("HTTP error text must not be read")

            response = io.BytesIO(b"synthetic body")
            error = UnprintableHTTPError("https://user:synthetic@fixture.invalid/?sig=" + REQUEST_MARK,
                                         403, SERVER_MARK, {}, response)
            self.addCleanup(error.close)
            output, diagnostic = io.StringIO(), io.StringIO()
            argv = ["watch_http_json.py", "--name", "events", "--url", "http://fixture.invalid/"]
            with self.subTest(error="HTTPError"), patch.object(sys, "argv", argv), \
                    patch.object(watcher.urllib.request, "urlopen", side_effect=error), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(diagnostic):
                self.assertEqual(watcher.main(), 2)
                self.assertEqual((output.getvalue(), diagnostic.getvalue()),
                                 ("", "watch_http_json: HTTP 403\n"))
                self.assertTrue(response.closed)

            cases = [
                (urllib.error.URLError(OSError(REQUEST_MARK)), "network error: URLError (OSError)"),
                (ValueError(REQUEST_MARK), "request error: ValueError"),
                (http.client.HTTPException(SERVER_MARK), "request error: HTTPException"),
                (http.client.InvalidURL(REQUEST_MARK), "request error: InvalidURL"),
                (http.client.IncompleteRead(b"partial"), "request error: IncompleteRead"),
                (RuntimeError(REQUEST_MARK), None),
                (KeyboardInterrupt(REQUEST_MARK), None),
            ]
            for exc, expected in cases:
                for boundary in ("urlopen", "read"):
                    with self.subTest(error=type(exc).__name__, boundary=boundary):
                        output, diagnostic = io.StringIO(), io.StringIO()
                        if boundary == "urlopen":
                            transport = patch.object(watcher.urllib.request, "urlopen", side_effect=exc)
                        else:
                            transport = patch.object(watcher.urllib.request, "urlopen")
                        with patch.object(sys, "argv", argv), transport as mock_open, \
                                contextlib.redirect_stdout(output), contextlib.redirect_stderr(diagnostic):
                            if boundary == "read":
                                mock_open.return_value.__enter__.return_value.read.side_effect = exc
                            if expected is None:
                                with self.assertRaises(type(exc)) as raised:
                                    watcher.main()
                                self.assertIs(raised.exception, exc)
                            else:
                                try:
                                    code = watcher.main()
                                except type(exc) as escaped:
                                    self.fail("named request error escaped: " + type(escaped).__name__)
                                self.assertEqual(code, 2)
                        self.assertEqual(output.getvalue(), "")
                        self.assertEqual(diagnostic.getvalue(),
                                         "watch_http_json: " + expected + "\n" if expected else "")
                        self.assertEqual(self.state.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
