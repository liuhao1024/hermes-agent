"""``hermes doctor`` Anthropic probe must target ``ANTHROPIC_BASE_URL``, not api.anthropic.com (#143023).

A gateway-issued token never validates against Anthropic directly, so the hardcoded
``GET api.anthropic.com/v1/models`` reported healthy gateways as "(invalid API key)". With a
custom base URL the probe must exercise the route the runtime uses — ``POST <base>/v1/messages`` —
where an Anthropic-shaped 400 still proves route + auth (same rule as the Foundry probe, #66756).
"""

from __future__ import annotations

import httpx

from hermes_cli import doctor_connectivity as dc

_GW = "https://gw.corp.example"


def _run_probe(monkeypatch, status: int, base_env: str | None, model_cfg: dict | None):
    calls: list = []

    def _post(url, headers=None, params=None, json=None, timeout=None):
        calls.append(("POST", url, headers, params, json))
        return httpx.Response(status, json={"type": "error", "error": {"type": "invalid_request_error", "message": "x"}}
                              if status == 400 else {"id": "msg_1"})

    def _get(url, headers=None, timeout=None):
        calls.append(("GET", url, headers, None, None))
        return httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error"}})

    monkeypatch.setattr(httpx, "post", _post)
    monkeypatch.setattr(httpx, "get", _get)
    import hermes_cli.auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_anthropic_key", lambda: "gw-token")
    if base_env:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", base_env)
    else:
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr(dc, "_model_cfg", lambda: model_cfg or {})
    return dc._probe_anthropic(), calls


def test_base_url_env_probes_gateway_messages_route_never_api_anthropic(monkeypatch):
    """Healthy gateway: one POST /v1/messages with x-api-key on the gateway host — never a GET to api.anthropic.com."""
    res, calls = _run_probe(monkeypatch, 200, base_env=_GW, model_cfg={"provider": "anthropic", "default": "claude-sonnet-5"})
    assert res.issues == [] and "\u2713" in res.lines[0][0], res
    assert [c[0] for c in calls] == ["POST"]
    _, url, headers, params, body = calls[0]
    assert url == _GW + "/v1/messages"
    assert headers["x-api-key"] == "gw-token" and "Authorization" not in headers
    assert headers["anthropic-version"] == "2023-06-01" and params is None
    assert body["max_tokens"] == 1 and body["model"] == "claude-sonnet-5"


def test_gateway_400_anthropic_shape_still_ok(monkeypatch):
    """A 400 from the gateway proves route + auth (#66756 rule): doctor must not call it a failure."""
    res, calls = _run_probe(monkeypatch, 400, base_env=_GW, model_cfg=None)
    assert res.issues == [] and "\u2713" in res.lines[0][0], res
    assert calls[0][1] == _GW + "/v1/messages"


def test_gateway_401_fails_against_the_gateway_not_api_anthropic(monkeypatch):
    """A gateway-rejected token fails on the gateway URL (the bug's false-positive path)."""
    res, calls = _run_probe(monkeypatch, 401, base_env=_GW, model_cfg=None)
    assert "\u2717" in res.lines[0][0] and "invalid API key" in res.lines[0][2], res
    assert calls[0][1] == _GW + "/v1/messages"


def test_config_model_base_url_is_probed_too(monkeypatch):
    """No env var, but config.yaml model.provider=anthropic + model.base_url: same gateway probe."""
    res, calls = _run_probe(monkeypatch, 200, base_env=None, model_cfg={"provider": "anthropic", "base_url": _GW + "/"})
    assert "\u2713" in res.lines[0][0] and calls[0][1] == _GW + "/v1/messages", (res, calls)


def test_no_override_keeps_native_models_probe(monkeypatch):
    """Without any override the probe stays on GET api.anthropic.com/v1/models (OAuth-aware path)."""
    res, calls = _run_probe(monkeypatch, 200, base_env=None, model_cfg={"provider": "openrouter"})
    assert [c[0] for c in calls] == ["GET"] and calls[0][1] == "https://api.anthropic.com/v1/models"
    assert "\u2717" in res.lines[0][0], res  # mocked native 401, unchanged behaviour
