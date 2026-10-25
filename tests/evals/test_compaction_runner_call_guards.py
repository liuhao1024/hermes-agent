"""Loud failure when the compaction eval's model calls come back empty (#144605).

A reasoning auxiliary model can spend the whole ``max_tokens`` budget on
reasoning and return ``content: ""``; the runner used to treat that as a
normal result (an empty search query that silently killed the recovery arm,
and a bare JSONDecodeError with no model/usage context).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from evals.compaction import runner


def _resp(content, *, finish_reason="stop", completion_tokens=10, reasoning_tokens=0):
    usage = SimpleNamespace(
        prompt_tokens=1,
        completion_tokens=completion_tokens,
        prompt_tokens_details=None,
        completion_tokens_details=(
            SimpleNamespace(reasoning_tokens=reasoning_tokens) if reasoning_tokens else None
        ),
    )
    return SimpleNamespace(
        usage=usage,
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
        ],
    )


def test_call_raises_on_empty_content_with_reason(monkeypatch):
    seen = {}

    def fake_call_llm(**kwargs):
        seen.update(kwargs)
        return _resp("", finish_reason="length", completion_tokens=100, reasoning_tokens=100)

    monkeypatch.setattr("agent.auxiliary_client.call_llm", fake_call_llm)

    with pytest.raises(RuntimeError) as excinfo:
        runner._call("prompt", max_tokens=100)

    message = str(excinfo.value)
    assert "finish_reason='length'" in message
    assert "reasoning_tokens=100" in message
    assert seen["max_tokens"] == 100


def test_call_returns_nonempty_content(monkeypatch):
    monkeypatch.setattr(
        "agent.auxiliary_client.call_llm", lambda **kwargs: _resp("ok query")
    )
    assert runner._call("prompt") == "ok query"


def test_generate_questions_wraps_unparseable_json(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "summarized_region", lambda cc, messages: [])
    monkeypatch.setattr(runner, "_call", lambda prompt, max_tokens=2000: "not json at all")

    with pytest.raises(RuntimeError) as excinfo:
        runner.generate_questions([], 3, tmp_path / "cache.json")

    assert "not json at all" in str(excinfo.value)
