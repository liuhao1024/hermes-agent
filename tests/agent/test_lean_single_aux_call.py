"""Lean compaction makes EXACTLY ONE auxiliary LLM request per attempt.

Contract (#96603 — the per-chunk digest loop made up to 28 extra aux calls
and pushed compactions to 7-11 minutes on slow aux routes):

(a) exactly one ``call_llm`` per compaction attempt in lean mode;
(b) the single response's detailed-session-log section lands in the summary;
(c) an oversized region is EVEN-SAMPLED into the one request's input (with
    explicit elision markers), never split into extra requests;
(d) the LLM-free anchor index and the session_search recovery footer are
    still appended.
"""

from unittest.mock import patch, MagicMock

import pytest

from agent.context_compressor import (
    ContextCompressor,
    _build_anchor_index,
    _LEAN_ANCHOR_HEADING,
    _LEAN_RECOVERY_HEADING,
    _LEAN_SESSION_LOG_HEADING,
)


def _mk_compressor(**overrides):
    kwargs = dict(
        model="test/model",
        threshold_percent=0.85,
        protect_first_n=2,
        protect_last_n=2,
        quiet_mode=True,
        tail_mode="lean",
    )
    kwargs.update(overrides)
    with patch(
        "agent.context_compressor.get_model_context_length", return_value=100_000
    ):
        c = ContextCompressor(**kwargs)
        _ = c.context_length
    c._session_id = "sess-lean-1call"
    return c


def _llm_response(text):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message = MagicMock()
    resp.choices[0].message.content = text
    return resp


def _big_region(n_rounds=60, tool_chars=6_000):
    """Synthetic compacted region: user + assistant/tool rounds, ~360K chars."""
    turns = [
        {"role": "user", "content": "Please fix PR #12345 in agent/foo.py"},
    ]
    for i in range(n_rounds):
        turns.append({
            "role": "assistant",
            "content": f"Working on step {i}: editing agent/foo.py line {i}",
        })
        turns.append({
            "role": "tool",
            "tool_call_id": f"tc{i}",
            "tool_name": "terminal",
            "content": f"round {i} output: " + ("x" * tool_chars),
        })
    return turns


SUMMARY_BODY = (
    "## Historical Task Snapshot\nUser asked: 'Please fix PR #12345 in agent/foo.py'\n\n"
    "## Goal\nFix the bug.\n\n"
    "## Completed Actions\n1. EDIT agent/foo.py — done [tool: patch]\n\n"
    f"{_LEAN_SESSION_LOG_HEADING}\n- Edited agent/foo.py; PR #12345; ran pytest.\n"
)


class TestLeanSingleAuxiliaryCall:
    def test_exactly_one_call_llm_per_lean_attempt(self):
        c = _mk_compressor()
        turns = _big_region()
        with patch(
            "agent.context_compressor.call_llm",
            return_value=_llm_response(SUMMARY_BODY),
        ) as main_call, patch(
            "agent.auxiliary_client.call_llm",
            return_value=_llm_response(SUMMARY_BODY),
        ) as aux_call:
            summary = c._generate_summary(turns)
        assert summary is not None
        # THE contract: one auxiliary request per compaction attempt, total.
        assert main_call.call_count + aux_call.call_count == 1

    def test_session_log_heading_lands_in_summary(self):
        c = _mk_compressor()
        turns = _big_region(n_rounds=5)
        with patch(
            "agent.context_compressor.call_llm",
            return_value=_llm_response(SUMMARY_BODY),
        ):
            summary = c._generate_summary(turns)
        assert _LEAN_SESSION_LOG_HEADING in summary


    def test_anchor_index_and_recovery_footer_present(self):
        c = _mk_compressor()
        turns = _big_region(n_rounds=5)
        with patch(
            "agent.context_compressor.call_llm",
            return_value=_llm_response(SUMMARY_BODY),
        ):
            summary = c._generate_summary(turns)
        assert _LEAN_ANCHOR_HEADING in summary
        assert _LEAN_RECOVERY_HEADING in summary
        assert "session_search" in summary

    def test_oversized_region_sampled_not_split_into_more_requests(self):
        c = _mk_compressor()
        turns = _big_region(n_rounds=120, tool_chars=6_000)  # ~720K chars raw
        with patch(
            "agent.context_compressor.call_llm",
            return_value=_llm_response(SUMMARY_BODY),
        ) as mock_call:
            c._generate_summary(turns)
        assert mock_call.call_count == 1
        prompt = mock_call.call_args.kwargs["messages"][0]["content"]
        # Bounded input with explicit elision markers, not a second request.
        assert len(prompt) <= c._SUMMARY_INPUT_MAX_CHARS + 20_000
        assert "chars elided" in prompt


class TestSampledSummaryInput:
    def test_small_input_passes_through(self):
        records = ["[USER]: abc" * 100] * 3
        assert ContextCompressor._sample_summary_records(records)[0] == "\n\n".join(records)

    def test_sampling_is_bounded_ordered_and_marked(self):
        # Distinct decade markers let us verify oldest-to-newest order and
        # uniform coverage across the whole region.
        records = [f"<seg{i:02d}>" + ("x" * 50_000) for i in range(10)]
        out, _coverage = ContextCompressor._sample_summary_records(records)
        assert len(out) <= ContextCompressor._SUMMARY_INPUT_MAX_CHARS
        assert "chars elided" in out
        seen = [i for i in range(10) if f"<seg{i:02d}>" in out]
        # Coverage reaches past the head AND includes the newest end.
        assert seen == sorted(seen)
        assert any(i >= 5 for i in seen)
        # The newest record anchors the last slice, so its token must be present outright.
        assert "<seg09>" in out

    def test_legacy_mode_keeps_head_tail_bound(self):
        c = _mk_compressor(tail_mode="legacy")
        turns = _big_region(n_rounds=120, tool_chars=6_000)
        with patch(
            "agent.context_compressor.call_llm",
            return_value=_llm_response("## Historical Task Snapshot\nNone."),
        ) as mock_call:
            c._generate_summary(turns)
        assert mock_call.call_count == 1
        prompt = mock_call.call_args.kwargs["messages"][0]["content"]
        assert "summary input truncated" in prompt


class TestAnchorCommitHarvest:
    """The commits: line must harvest real SHAs only (#141267): no hyphen-cut
    UUID tail segments, no bare digit runs (Windows build numbers, ticket ids)."""

    @staticmethod
    def _commits_line(text):
        idx = _build_anchor_index([{"role": "user", "content": text}])
        for line in idx.splitlines():
            if line.startswith("commits:"):
                return line
        return ""

    def test_uuid_tail_segments_are_not_commits(self):
        line = self._commits_line(
            "memory d4e74cab-c16c-4e5b-a434-5aad31e0988f and "
            "d3a24c89-3983-4943-96ec-ccc8db9e44c1 saved"
        )
        assert "5aad31e0988f" not in line
        assert "ccc8db9e44c1" not in line

    def test_bare_digit_runs_are_not_commits(self):
        line = self._commits_line("forum thread 262209022 and ticket 123456789")
        assert line == ""

    def test_real_shas_are_still_harvested(self):
        line = self._commits_line("landed in 3752497cc4 then reverted by aa74e184ea0")
        assert "3752497cc4" in line
        assert "aa74e184ea0" in line

    def test_hex_glued_into_a_longer_word_stays_out(self):
        line = self._commits_line("prefixzz3752497cc4")
        assert "3752497cc4" not in line


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
