"""``ProviderProfile.fetch_models`` must decompress a gzip /models body.

Profiles that forward ``Accept-Encoding: gzip`` via ``default_headers``
(e.g. kimi-coding) get a gzipped body back from providers that honor it,
and ``urllib`` never auto-decompresses. Pre-fix the ``UnicodeDecodeError``
was swallowed by the broad ``except`` and the live catalog silently
degraded to the static curated list (#144328).
"""

import gzip
import json
from unittest.mock import MagicMock, patch

from providers.base import ProviderProfile

# Synthetic bearer value for the mocked fetch (never a real credential).
_API_KEY = f"test-{'key'}"


def _response(body: bytes, content_encoding: str | None) -> MagicMock:
    response = MagicMock()
    ctx = response.__enter__.return_value
    ctx.read.return_value = body
    ctx.headers.get.return_value = content_encoding
    return response


def _gzipped_models(items: list[dict]) -> bytes:
    return gzip.compress(json.dumps({"data": items}).encode())


def _plain_models(items: list[dict]) -> bytes:
    return json.dumps({"data": items}).encode()


def _profile() -> ProviderProfile:
    return ProviderProfile(
        name="gzip-probe",
        base_url="https://gzip.example.test/v1",
        default_headers={"Accept-Encoding": "gzip"},
    )


def test_fetch_models_decompresses_a_gzip_body():
    profile = _profile()
    response = _response(_gzipped_models([{"id": "kimi-k3.1-pro"}]), "gzip")
    with patch("hermes_cli.urllib_security.open_credentialed_url", return_value=response):
        assert profile.fetch_models(api_key=_API_KEY) == ["kimi-k3.1-pro"]


def test_fetch_models_tolerates_header_case_and_whitespace():
    profile = _profile()
    response = _response(_gzipped_models([{"id": "kimi-k3.1-pro"}]), " GZIP ")
    with patch("hermes_cli.urllib_security.open_credentialed_url", return_value=response):
        assert profile.fetch_models(api_key=_API_KEY) == ["kimi-k3.1-pro"]


def test_fetch_models_keeps_plain_body_when_not_gzipped():
    profile = _profile()
    response = _response(_plain_models([{"id": "kimi-k3"}]), None)
    with patch("hermes_cli.urllib_security.open_credentialed_url", return_value=response):
        assert profile.fetch_models(api_key=_API_KEY) == ["kimi-k3"]


def test_fetch_models_still_fails_closed_on_corrupt_gzip():
    """A body labeled gzip that does not decompress still degrades to None —
    the fix must not widen the failure surface."""
    profile = _profile()
    response = _response(b"\x1f\x8b-not-really-gzip", "gzip")
    with patch("hermes_cli.urllib_security.open_credentialed_url", return_value=response):
        assert profile.fetch_models(api_key=_API_KEY) is None
