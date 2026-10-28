"""Regression tests for the osint-investigation optional skill scripts (#145890).

The scripts live outside the test package tree (they ship with the optional
skill), so they are loaded by path. All tests are offline: they exercise the
pure helpers and the HTTP error/trust handling with mocks.
"""

import importlib.util
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "optional-skills" / "research" / "osint-investigation" / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_osint_test_{name}", SCRIPTS / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def http_mod():
    return _load("_http")


@pytest.fixture
def ofac_mod():
    return _load("fetch_ofac_sdn")


@pytest.fixture
def edgar_mod():
    return _load("fetch_sec_edgar")


# ── 1. OFAC: blank sdn_type (null "-0-") means "entity" ─────────────────────


def test_ofac_blank_sdn_type_maps_to_entity(ofac_mod):
    assert ofac_mod._resolve_sdn_type("") == "entity"
    assert ofac_mod._resolve_sdn_type("-0-") == "entity"


def test_ofac_known_types_still_map(ofac_mod):
    assert ofac_mod._resolve_sdn_type("individual") == "individual"
    assert ofac_mod._resolve_sdn_type("Vessel") == "vessel"
    assert ofac_mod._resolve_sdn_type("aircraft") == "aircraft"


def test_ofac_blank_type_survives_entity_filter(ofac_mod):
    # The regression: `--entity-type entity` used to drop every entity row
    # because their blank type compared unequal to "entity".
    rows = [
        {"sdn_type": "-0-", "sdn_name": "ACME Ltd", "ent_num": "1"},
        {"sdn_type": "individual", "sdn_name": "Bad Person", "ent_num": "2"},
    ]
    kept = [
        r["sdn_name"]
        for r in rows
        if ofac_mod._resolve_sdn_type(r["sdn_type"]) == "entity"
    ]
    assert kept == ["ACME Ltd"]


# ── 2. SEC EDGAR: HERMES_OSINT_UA is accepted as the contact UA ─────────────


def test_edgar_ua_prefers_hermes_osint_ua(edgar_mod, monkeypatch):
    monkeypatch.setenv("HERMES_OSINT_UA", "skill-ua/1.0 (a@b.c)")
    monkeypatch.setenv("SEC_USER_AGENT", "sec-ua/1.0 (a@b.c)")
    assert edgar_mod._ua() == "skill-ua/1.0 (a@b.c)"


def test_edgar_ua_falls_back_to_sec_user_agent(edgar_mod, monkeypatch):
    monkeypatch.delenv("HERMES_OSINT_UA", raising=False)
    monkeypatch.setenv("SEC_USER_AGENT", "sec-ua/1.0 (a@b.c)")
    assert edgar_mod._ua() == "sec-ua/1.0 (a@b.c)"


def test_edgar_ua_still_fails_without_any(edgar_mod, monkeypatch):
    monkeypatch.delenv("HERMES_OSINT_UA", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SystemExit):
        edgar_mod._ua()


# ── 3. 401/403 surface an actionable message instead of a bare traceback ────


def _http_error(code: int) -> Exception:
    import urllib.error

    return urllib.error.HTTPError(
        url="https://example.invalid/api", code=code, msg="denied", hdrs=None, fp=None
    )


@pytest.mark.parametrize("code", [401, 403])
def test_http_401_403_raise_key_hint(http_mod, code, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", mock.Mock(side_effect=_http_error(code))
    )
    with pytest.raises(RuntimeError) as excinfo:
        http_mod.get("https://example.invalid/api")
    msg = str(excinfo.value)
    assert f"HTTP {code}" in msg
    assert "--token" in msg
    assert "requires an API key" in msg


def test_http_429_hint_unchanged(http_mod, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", mock.Mock(side_effect=_http_error(429))
    )
    with pytest.raises(RuntimeError) as excinfo:
        http_mod.get("https://example.invalid/api")
    assert "429 rate-limited" in str(excinfo.value)


# ── 4. TLS: explicit trust chain when the child lacks the agent's patch ─────


def test_ssl_context_prefers_ssl_cert_file(http_mod, monkeypatch, tmp_path):
    bundle = tmp_path / "bundle.pem"
    bundle.write_text("")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    with mock.patch.object(
        http_mod.ssl, "create_default_context", return_value=mock.Mock()
    ) as mk:
        ctx = http_mod._ssl_context()
    assert ctx is not None
    mk.assert_called_once_with(cafile=str(bundle))


def test_ssl_context_returns_none_without_certifi(http_mod, monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    with mock.patch.dict(sys.modules, {"certifi": None}):
        assert http_mod._ssl_context() is None


def test_ssl_context_uses_certifi_when_importable(http_mod, monkeypatch, tmp_path):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    fake = mock.Mock()
    bundle = tmp_path / "cacert.pem"
    bundle.write_text("")
    fake.where.return_value = str(bundle)
    with mock.patch.dict(sys.modules, {"certifi": fake}):
        with mock.patch.object(
            http_mod.ssl, "create_default_context", return_value=mock.Mock()
        ) as mk:
            ctx = http_mod._ssl_context()
    assert ctx is not None
    fake.where.assert_called_once()
    mk.assert_called_once_with(cafile=str(bundle))
