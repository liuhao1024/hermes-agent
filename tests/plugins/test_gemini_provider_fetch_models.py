"""Test GeminiProfile.fetch_models() query-param auth override."""

import json
from unittest.mock import patch

import pytest


def test_gemini_fetch_models_uses_query_param_auth():
    """Gemini's native /v1beta endpoint rejects Bearer auth; must use ?key= query param."""
    from plugins.model_providers.gemini import gemini

    # Mock urllib.request.urlopen to inspect the request
    captured_requests = []

    class MockResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({
                "models": [
                    {"name": "models/gemini-2.5-pro"},
                    {"name": "models/gemini-2.5-flash"},
                    {"name": "models/gemini-2.0-flash-exp"},
                ]
            }).encode()

    def mock_urlopen(req, **kwargs):
        captured_requests.append(req)
        return MockResponse()

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        model_ids = gemini.fetch_models(api_key="test-key-12345")

    # Should successfully parse and strip "models/" prefix
    assert model_ids == [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash-exp",
    ]

    # Verify the request used query-param auth, not Bearer header
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert "test-key-12345" in req.full_url
    assert "?key=" in req.full_url
    # Bearer header should NOT be present
    for (header, value) in req.headers.items():
        if header.lower() == "authorization":
            pytest.fail(f"Authorization header found: {value} (should use query param instead)")


def test_gemini_fetch_models_without_api_key():
    """fetch_models returns None when no API key is provided."""
    from plugins.model_providers.gemini import gemini

    result = gemini.fetch_models(api_key=None)
    assert result is None


def test_gemini_fetch_models_handles_prefix_stripping():
    """Gemini response uses "name" field with "models/" prefix; we strip it."""
    from plugins.model_providers.gemini import gemini

    class MockResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({
                "models": [
                    {"name": "models/gemini-1.5-pro"},
                    {"name": "models/embedding-001"},
                ]
            }).encode()

    def mock_urlopen(req, **kwargs):
        return MockResponse()

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        model_ids = gemini.fetch_models(api_key="test-key")

    # Prefixes are stripped
    assert model_ids == ["gemini-1.5-pro", "embedding-001"]


def test_gemini_fetch_models_handles_non_prefixed_names():
    """If a model name lacks "models/" prefix, use it as-is (defensive)."""
    from plugins.model_providers.gemini import gemini

    class MockResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({
                "models": [
                    {"name": "models/gemini-2.5-pro"},
                    {"name": "text-bison-001"},  # No prefix
                ]
            }).encode()

    def mock_urlopen(req, **kwargs):
        return MockResponse()

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        model_ids = gemini.fetch_models(api_key="test-key")

    # One stripped, one used as-is
    assert model_ids == ["gemini-2.5-pro", "text-bison-001"]


def test_gemini_fetch_models_fallback_on_error():
    """On any exception, fetch_models returns None (caller falls back to static list)."""
    from plugins.model_providers.gemini import gemini

    def mock_urlopen(req, **kwargs):
        raise TimeoutError("Connection timed out")

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        result = gemini.fetch_models(api_key="test-key")

    assert result is None


def test_gemini_fetch_models_uses_custom_base_url():
    """When base_url is overridden, use it instead of profile's default."""
    from plugins.model_providers.gemini import gemini

    captured_urls = []

    class MockResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({"models": []}).encode()

    def mock_urlopen(req, **kwargs):
        captured_urls.append(req.full_url)
        return MockResponse()

    custom_url = "https://custom-gateway.example.com/v1beta"
    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        gemini.fetch_models(api_key="test-key", base_url=custom_url)

    assert len(captured_urls) == 1
    # Should use custom URL with query param
    assert custom_url in captured_urls[0]
    assert "?key=" in captured_urls[0]