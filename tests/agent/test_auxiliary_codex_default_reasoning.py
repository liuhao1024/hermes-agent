from types import SimpleNamespace

from agent.auxiliary_client import _CodexCompletionsAdapter


def _resp_kwargs(extra_body=None, model="gpt-5.6-sol", base_url="https://example.com/v1"):
    adapter = _CodexCompletionsAdapter(SimpleNamespace(base_url=base_url), model)
    kwargs = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
    if extra_body is not None:
        kwargs["extra_body"] = extra_body
    resp_kwargs, _, _ = adapter._build_responses_kwargs(kwargs)
    return resp_kwargs


def test_unset_reasoning_defaults_to_medium_summary_auto():
    """#143284: an auxiliary Codex call with no reasoning config must still request a
    reasoning summary, like codex.py::build_kwargs() does on the main transport. Without
    it a reasoning model thinks with zero summary events and _CodexStreamGuard's
    no-progress window aborts the call at 60s."""
    resp_kwargs = _resp_kwargs(extra_body={})
    assert resp_kwargs["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert resp_kwargs["include"] == ["reasoning.encrypted_content"]


def test_missing_extra_body_entirely_also_defaults_reasoning():
    # The default for vision/compression and every other unconfigured aux task is no
    # extra_body key at all.
    resp_kwargs = _resp_kwargs()
    assert resp_kwargs["reasoning"] == {"effort": "medium", "summary": "auto"}


def test_unconfigured_aux_call_matches_main_transport_reasoning():
    # Parity with the main agent transport for the same unconfigured call: build_kwargs()
    # resolves effort "medium"/enabled and emits {"effort": "medium", "summary": "auto"}.
    from agent.codex_responses_adapter import classify_responses_route
    from agent.transports.codex import ResponsesApiTransport

    base_url = "https://example.com/v1"
    route = classify_responses_route(SimpleNamespace(provider=None, base_url=base_url))
    main_kwargs = ResponsesApiTransport().build_kwargs(
        "gpt-5.6-sol", [{"role": "user", "content": "hi"}], None,
        base_url=base_url, **route._asdict(),
    )
    assert main_kwargs["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert _resp_kwargs(extra_body={})["reasoning"] == main_kwargs["reasoning"]


def test_explicit_effort_still_wins():
    resp_kwargs = _resp_kwargs(extra_body={"reasoning": {"effort": "high"}})
    assert resp_kwargs["reasoning"] == {"effort": "high", "summary": "auto"}


def test_explicit_disable_still_goes_on_the_wire_as_none():
    resp_kwargs = _resp_kwargs(extra_body={"reasoning": {"enabled": False}})
    assert resp_kwargs["reasoning"] == {"effort": "none"}


def test_model_that_takes_no_reasoning_field_still_sends_none():
    """#76255 regression guard: gpt-4o on api.openai.com 400s on any reasoning key."""
    resp_kwargs = _resp_kwargs(model="gpt-4o", base_url="https://api.openai.com/v1", extra_body={})
    assert "reasoning" not in resp_kwargs
