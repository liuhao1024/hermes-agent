"""The aux custom arm sends the endpoint's own same-origin credential ahead of the ambient
OPENAI_API_KEY: a main route declared on a third-party base_url must not see the OpenAI
secret replaced by... itself — the route's key_env credential wins (#145452). A mismatched
endpoint never inherits the route's key, and with no OpenAI key at all the placeholder stands."""

from unittest.mock import MagicMock, patch

import agent.auxiliary_client as ac


def _resolve_custom_sent_key(
    monkeypatch, *, main_base, main_key, ambient_openai, endpoint
):
    monkeypatch.setenv("OPENAI_API_KEY", ambient_openai)
    created = {}

    def create(**kwargs):
        created.update(kwargs)
        return MagicMock()

    with (
        patch.object(ac, "_normalize_main_runtime", return_value={}),
        patch.object(ac, "_read_main_base_url", return_value=main_base),
        patch.object(ac, "_read_main_api_key", return_value=main_key),
        patch("hermes_cli.config.load_config", return_value={}),
        patch("hermes_cli.config.load_config_readonly", return_value={}),
        patch.object(ac, "_create_openai_client", side_effect=create),
    ):
        client, _model = ac.resolve_provider_client(
            "custom", model="m", explicit_base_url=endpoint
        )
    assert client is not None, "no client was built"
    return created.get("api_key")


def test_route_own_same_origin_key_beats_ambient_openai_key(monkeypatch):
    sent = _resolve_custom_sent_key(
        monkeypatch,
        main_base="https://litellm.internal:4000/v1",
        main_key="sk-litellm-route",
        ambient_openai="sk-openai-ambient",
        endpoint="https://litellm.internal:4000/v1",
    )
    assert sent == "sk-litellm-route"


def test_openai_base_url_flow_still_uses_the_openai_key(monkeypatch):
    sent = _resolve_custom_sent_key(
        monkeypatch,
        main_base="https://api.openai.com/v1",
        main_key="sk-openai-main",
        ambient_openai="sk-openai-main",
        endpoint="https://api.openai.com/v1",
    )
    assert sent == "sk-openai-main"


def test_mismatched_endpoint_never_inherits_the_main_routes_key(monkeypatch):
    sent = _resolve_custom_sent_key(
        monkeypatch,
        main_base="https://litellm.internal:4000/v1",
        main_key="sk-litellm-route",
        ambient_openai="sk-openai-ambient",
        endpoint="https://other.internal:4000/v1",
    )
    assert sent != "sk-litellm-route"


def test_mismatched_endpoint_without_any_openai_key_sends_the_placeholder(monkeypatch):
    sent = _resolve_custom_sent_key(
        monkeypatch,
        main_base="https://litellm.internal:4000/v1",
        main_key="sk-litellm-route",
        ambient_openai="",
        endpoint="https://other.internal:4000/v1",
    )
    assert sent == "no-key-required"
