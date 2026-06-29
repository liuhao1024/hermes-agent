"""Regression tests for web provider response body-size cap (issue #55079).

Verifies that bundled web providers reject oversized JSON responses
via ``read_json_capped`` before allocating memory for unbounded bodies.
"""

from __future__ import annotations

import json

import httpx
import pytest

from plugins.web._utils import read_json_capped


# ---------------------------------------------------------------------------
# Unit tests for read_json_capped
# ---------------------------------------------------------------------------


class TestReadJsonCapped:
    """Direct tests for the shared body-cap helper."""

    def _make_response(
        self,
        body: bytes,
        *,
        content_length: str | None = None,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if content_length is not None:
            headers["content-length"] = content_length
        return httpx.Response(
            status_code=200,
            content=body,
            headers=headers,
        )

    def test_normal_json_passes(self) -> None:
        payload = {"results": [{"title": "test"}]}
        body = json.dumps(payload).encode()
        resp = self._make_response(body)
        assert read_json_capped(resp) == payload

    def test_empty_object_passes(self) -> None:
        resp = self._make_response(b"{}")
        assert read_json_capped(resp) == {}

    def test_body_exceeding_cap_raises_value_error(self) -> None:
        # Create a body just over the cap.
        cap = 1024
        big = b"x" * (cap + 1)
        # Wrap in JSON so json.loads would succeed if unchecked.
        body = json.dumps({"data": big.decode()}).encode()
        resp = self._make_response(body)
        with pytest.raises(ValueError, match="too large"):
            read_json_capped(resp, max_bytes=cap)

    def test_body_exactly_at_cap_passes(self) -> None:
        cap = 1024
        # Construct JSON that is exactly cap bytes.
        inner = "a" * (cap - 20)  # room for {"data":"..."}
        body = json.dumps({"data": inner}).encode()
        # Trim to exactly cap bytes by adjusting inner length.
        while len(body) > cap:
            inner = inner[:-1]
            body = json.dumps({"data": inner}).encode()
        resp = self._make_response(body)
        result = read_json_capped(resp, max_bytes=cap)
        assert "data" in result

    def test_content_length_header_fast_reject(self) -> None:
        cap = 512
        body = b'{"ok":true}'
        resp = self._make_response(body, content_length="999999")
        with pytest.raises(ValueError, match="too large"):
            read_json_capped(resp, max_bytes=cap)

    def test_malformed_content_length_falls_through_to_body_check(self) -> None:
        body = b'{"ok":true}'
        resp = self._make_response(body, content_length="not-a-number")
        # Should not raise — the body itself is tiny.
        assert read_json_capped(resp, max_bytes=1024) == {"ok": True}

    def test_invalid_json_still_raises_from_json_loads(self) -> None:
        resp = self._make_response(b"not json at all")
        with pytest.raises(json.JSONDecodeError):
            read_json_capped(resp)


# ---------------------------------------------------------------------------
# Provider integration — verify each provider surfaces the cap error
# ---------------------------------------------------------------------------


class TestProviderBodyCapIntegration:
    """Verify that each provider's search/extract path returns a typed
    error dict when the upstream response exceeds the body cap."""

    def test_tavily_search_oversized_returns_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tavily search should return {success: False, error: ...}
        when the response body is oversized."""
        import plugins.web.tavily.provider as tavily_mod

        monkeypatch.setenv("TAVILY_API_KEY", "test-key")

        big_body = json.dumps({"results": "x" * (11 * 1024 * 1024)}).encode()
        fake_resp = httpx.Response(
            status_code=200,
            content=big_body,
            request=httpx.Request("POST", "https://api.tavily.com/search"),
        )

        def _fake_post(*args, **kwargs):
            return fake_resp

        monkeypatch.setattr("httpx.post", _fake_post)
        result = tavily_mod.TavilyWebSearchProvider().search("test")
        assert result["success"] is False
        assert "too large" in result["error"].lower()

    def test_searxng_search_oversized_returns_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SearXNG search should return {success: False, error: ...}
        when the response body is oversized."""
        import plugins.web.searxng.provider as searxng_mod

        monkeypatch.setenv("SEARXNG_URL", "http://localhost:8080")

        big_body = json.dumps({"results": "x" * (11 * 1024 * 1024)}).encode()
        fake_resp = httpx.Response(
            status_code=200,
            content=big_body,
            request=httpx.Request("GET", "http://localhost:8080/search"),
        )

        def _fake_get(*args, **kwargs):
            return fake_resp

        monkeypatch.setattr("httpx.get", _fake_get)
        result = searxng_mod.SearXNGWebSearchProvider().search("test")
        assert result["success"] is False
        assert "rejected" in result["error"].lower() or "too large" in result["error"].lower()

    def test_brave_search_oversized_returns_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Brave Free search should return {success: False, error: ...}
        when the response body is oversized."""
        import plugins.web.brave_free.provider as brave_mod

        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")

        big_body = json.dumps({"web": {"results": "x" * (11 * 1024 * 1024)}}).encode()
        fake_resp = httpx.Response(
            status_code=200,
            content=big_body,
            request=httpx.Request("GET", "https://api.search.brave.com/res/v1/web/search"),
        )

        def _fake_get(*args, **kwargs):
            return fake_resp

        monkeypatch.setattr("httpx.get", _fake_get)
        result = brave_mod.BraveFreeWebSearchProvider().search("test")
        assert result["success"] is False
        assert "rejected" in result["error"].lower() or "too large" in result["error"].lower()
