"""#144896: "stream" as a substring also lives inside "upstream"/"downstream" and hyphen
compounds like "stream-json", so an ordinary field-rejection 400 wrapped as "upstream said:
X is not supported" classified as a streaming rejection and disabled streaming for the rest
of the session. Only an error that rejects *streaming* itself — the whole word stream/
streams/streaming, no hyphen compounds — may flip the flag."""

from types import SimpleNamespace

import pytest

UPSTREAM_WRAPPER = "Incomplete upstream response (first attempt: status 400, upstream said: {})"


class TestIsStreamUnavailableErrorWording:
    def _judge(self, text: str) -> bool:
        from agent.anthropic_adapter import _is_stream_unavailable_error

        return _is_stream_unavailable_error(RuntimeError(text))

    @pytest.mark.parametrize("body", [
        "adaptive thinking is not supported on this model",
        "For 'integer' type, properties maximum, minimum are not supported",
    ])
    def test_upstream_wrapper_quoting_an_unrelated_rejection_is_not_stream_unavailable(self, body):
        assert self._judge(UPSTREAM_WRAPPER.format(body)) is False

    def test_stderr_stream_json_tail_is_not_a_streaming_rejection(self):
        text = ("Native history replay not supported: model cannot resume\n"
                "native stderr: Error: --input-format stream-json requires a pty")
        assert self._judge(text) is False

    @pytest.mark.parametrize("body", [
        "streaming is not supported",
        "stream not supported for this model",
        "Stream mode is not supported",
        "streams are not supported for this endpoint",
        "this provider says streaming is not supported (api error)",
    ])
    def test_genuine_streaming_rejections_still_fall_back(self, body):
        assert self._judge(UPSTREAM_WRAPPER.format(body)) is True
        assert self._judge(body) is True

    def test_unexpected_event_order_still_falls_back(self):
        assert self._judge("Anthropic stream ended: unexpected event order") is True


class TestMaybeDisableStreaming:
    def _call(self, agent):
        from agent.chat_completion_helpers import _StreamingCall

        return SimpleNamespace(agent=agent, deltas_were_sent={"yes": False}, api_kwargs={"model": "m"},
                               _stream_stale_timeout=900.0, result={"error": None, "response": None})

    def _agent(self):
        return SimpleNamespace(_interrupt_requested=False, provider="custom",
                               base_url="https://relay.example/v1", api_mode="anthropic_messages",
                               _disable_streaming=False, _safe_print=lambda *a, **k: None,
                               _emit_warning=lambda *a, **k: None)

    def test_upstream_wording_leaves_streaming_on(self):
        from agent.chat_completion_helpers import _StreamingCall

        agent = self._agent()
        _StreamingCall._maybe_disable_streaming(
            self._call(agent),
            RuntimeError(UPSTREAM_WRAPPER.format("adaptive thinking is not supported on this model")))
        assert agent._disable_streaming is False

    def test_genuine_rejection_still_disables_streaming(self):
        from agent.chat_completion_helpers import _StreamingCall

        agent = self._agent()
        _StreamingCall._maybe_disable_streaming(
            self._call(agent), RuntimeError("streaming is not supported for this model"))
        assert agent._disable_streaming is True
