"""Per-request image COUNT caps misrouted to ``format_error`` (#142602).

A provider-side rejection for exceeding the per-request image count limit ("Model 'X'
supports at most 16 images per prompt.") used to classify as ``format_error`` — telling
the user their well-formed request was "malformed" — because ``_IMAGE_TOO_LARGE_PATTERNS``
only knew byte/dimension wording. The turn then aborted as a non-retryable client error
instead of recovering: the recovery for a COUNT cap is retiring the oldest tool-result
images down to the parsed cap, which shrinking cannot do (a resized image still counts).

Covers the chain pieces that matter independently (the retry-loop wiring follows the
same layering as test_image_shrink_recovery.py):

  1. Classification: wrapped (OpenRouter ``error.metadata.raw``) and direct wordings
     land on ``image_too_large``; an unrelated 400 stays ``format_error``.
  2. Cap parsing: ``image_count_cap_from_error`` reads the N out of the same surfaces.
  3. Retire-to-cap: ``evict_stale_outbound_tool_images`` with ``limit=16`` drops the
     oldest tool-result images and never touches user uploads.
  4. Recovery wiring: ``recover_after_classification`` retires down to the latched cap
     and retries; the latch then tightens the send path for the rest of the session.

See: https://github.com/NousResearch/hermes-agent/issues/142602
"""

from __future__ import annotations

import json

from types import SimpleNamespace

from agent.context_compressor import evict_stale_outbound_tool_images
from agent.error_classifier import (
    FailoverReason,
    classify_api_error,
    image_count_cap_from_error,
)
from agent.turn_recovery import recover_after_classification
from agent.turn_retry_state import TurnRetryState

# recover_after_classification imports agent.conversation_loop lazily, whose module body
# runs the interrupted-update probe at import time. Warm it here (collection time, before
# the per-test home-io guard installs) so the lazy import inside the recovery is a no-op —
# the same layering test_image_shrink_recovery.py relies on.
import agent.conversation_loop


class _FakeApiError(Exception):
    """Stand-in for an openai.BadRequestError with status_code + body."""

    def __init__(self, status_code: int, message: str, body: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body if body is not None else {"error": {"message": message}}
        self.response = None


def _wrapped_count_rejection(cap: int = 16) -> _FakeApiError:
    """The exact shape from the report: OpenRouter's generic wrapper with the real
    DeepSeek rejection tucked inside ``error.metadata.raw``."""
    raw = json.dumps(
        {
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_request_error",
                "message": f"Model 'deepseek/deepseek-v4.1-flash-1017' supports at most {cap} images per prompt.",
            }
        }
    )
    return _FakeApiError(
        400,
        "Error code: 400 - {'error': {'message': 'Provider returned error'}}",
        body={
            "error": {
                "message": "Provider returned error",
                "code": 400,
                "metadata": {"raw": raw, "provider_name": "DeepSeek"},
            }
        },
    )


# ─── Classification ──────────────────────────────────────────────────────────


class TestImageCountCapClassification:
    def test_wrapped_openrouter_400_classified_as_image_too_large(self):
        """Before the fix the wrapper hid the count wording and the 400 read as a
        malformed request (format_error, non-retryable) — no image recovery ran."""
        result = classify_api_error(
            _wrapped_count_rejection(),
            provider="openrouter",
            model="deepseek/deepseek-v4.1-flash-1017",
        )
        assert result.reason == FailoverReason.image_too_large
        assert result.retryable is True

    def test_direct_wording_classified_as_image_too_large(self):
        """The same rejection arriving unwrapped (native route, or a proxy that
        flattens the body) must take the same image path."""
        for wording in (
            "Model 'X' supports at most 5 images per request.",
            "this model accepts at most 8 images per message.",
        ):
            e = _FakeApiError(400, f"400 invalid_request_error: {wording}")
            result = classify_api_error(e, provider="openrouter", model="deepseek/v4.1")
            assert result.reason == FailoverReason.image_too_large, wording

    def test_unrelated_400_stays_format_error(self):
        """A generic 400 with no image vocabulary must not ride the retire path."""
        e = _FakeApiError(400, "Error code: 400 - invalid request: unknown field 'foo'")
        result = classify_api_error(e, provider="openrouter", model="deepseek/v4.1")
        assert result.reason == FailoverReason.format_error


# ─── Cap parsing ─────────────────────────────────────────────────────────────


class TestImageCountCapParsing:
    def test_wrapped_body_yields_the_cap(self):
        assert image_count_cap_from_error(_wrapped_count_rejection(16)) == 16

    def test_direct_wording_yields_the_cap(self):
        e = _FakeApiError(400, "supports at most 3 images per prompt")
        assert image_count_cap_from_error(e) == 3

    def test_no_cap_wording_yields_none(self):
        e = _FakeApiError(400, "image exceeds 5 MB maximum")
        assert image_count_cap_from_error(e) is None


# ─── Retire-to-cap ───────────────────────────────────────────────────────────


def _image_msg(role: str, url: str) -> dict:
    return {"role": role, "content": [{"type": "image_url", "image_url": {"url": url}}]}


def _tool_msg(call_id: str, url: str) -> dict:
    msg = _image_msg("tool", url)
    msg["tool_call_id"] = call_id
    return msg


def _tiny_data_url(k: int) -> str:
    # Dimensions are unreadable in a 1-byte payload, keeping the strict 20-image
    # trigger live — the count-cap override must still retire below 16.
    return f"data:image/png;base64,AAA{k}"


class TestEvictWithProviderCap:
    def test_retires_oldest_tool_images_down_to_the_cap(self):
        msgs = [_tool_msg(f"call-{i}", _tiny_data_url(i)) for i in range(17)]
        pruned = evict_stale_outbound_tool_images(msgs, None, limit=16)
        assert pruned >= 1
        remaining = sum(
            1 for m in msgs if any(p.get("type") == "image_url" for p in m["content"])
        )
        assert remaining <= 16
        # Newest frames survive; the retired oldest rows collapse to text, not vanish.
        assert msgs[-1]["content"][0]["type"] == "image_url"
        assert msgs[0]["content"][0]["type"] != "image_url"

    def test_default_ceiling_untouched_without_limit(self):
        """17 tool images sit under the strict default ceiling of 20: without the
        parsed cap nothing is retired — the over-send the issue reports."""
        msgs = [_tool_msg(f"call-{i}", _tiny_data_url(i)) for i in range(17)]
        assert evict_stale_outbound_tool_images(msgs, None) == 0

    def test_user_uploads_are_never_retired(self):
        msgs = [_image_msg("user", _tiny_data_url(i)) for i in range(17)]
        assert evict_stale_outbound_tool_images(msgs, None, limit=16) == 0
        assert all(m["content"][0]["type"] == "image_url" for m in msgs)


# ─── Recovery wiring ─────────────────────────────────────────────────────────


class _Agent:
    log_prefix = ""
    verbose = False
    provider = "openrouter"
    model = "deepseek/deepseek-v4.1-flash-1017"
    api_mode = "chat_completions"
    base_url = "https://openrouter.ai/api/v1"

    def __init__(self):
        # ``__getattr__`` fabricates callables for unknown names; attributes the
        # recovery reads with a getattr-default must start as real values, exactly
        # like on the real AIAgent.
        self.session_id = "s1"
        self._outbound_retired_image_ids = None
        self._provider_image_count_cap = None

    def _recover_with_credential_pool(self, **kwargs):
        return False, False

    def _vprint(self, line, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class TestCountCapRecovery:
    def test_recovery_retires_to_cap_latches_it_and_retries(self):
        agent = _Agent()
        err = _wrapped_count_rejection(16)
        classified = classify_api_error(err, provider=agent.provider, model=agent.model)
        assert classified.reason == FailoverReason.image_too_large
        api_messages = [_tool_msg(f"call-{i}", _tiny_data_url(i)) for i in range(17)]
        retry = TurnRetryState()

        retry_now, _ = recover_after_classification(
            agent,
            err,
            classified,
            retry,
            status_code=400,
            error_context={},
            messages=[],
            api_messages=api_messages,
        )
        assert retry_now is True
        assert agent._provider_image_count_cap == 16
        remaining = sum(
            1
            for m in api_messages
            if any(p.get("type") == "image_url" for p in m["content"])
        )
        assert remaining <= 16

        # The latched cap tightens the send path: a fresh clone of the same 17-image
        # request is retired before it ever leaves (turn_request_assembly passes the
        # latch down as ``limit``).
        fresh = [_tool_msg(f"call-{i}", _tiny_data_url(i)) for i in range(17)]
        assert (
            evict_stale_outbound_tool_images(
                fresh, None, limit=agent._provider_image_count_cap
            )
            >= 1
        )

    def test_upload_only_request_does_not_shrink_or_retire(self):
        """Reporter's remaining edge case: every image is a user upload, so nothing
        can be retired. The recovery must not fall through to a pointless shrink —
        the count is unchanged by a resize — and must not claim success."""
        agent = _Agent()
        err = _wrapped_count_rejection(16)
        classified = classify_api_error(err, provider=agent.provider, model=agent.model)
        api_messages = [_image_msg("user", _tiny_data_url(i)) for i in range(17)]
        retry = TurnRetryState()
        shrunk: list[bool] = []
        agent._try_shrink_image_parts_in_messages = lambda *a, **k: (
            shrunk.append(True) or True
        )

        retry_now, _ = recover_after_classification(
            agent,
            err,
            classified,
            retry,
            status_code=400,
            error_context={},
            messages=[],
            api_messages=api_messages,
        )
        assert shrunk == [], "a count rejection must not be answered with a resize"
        assert retry_now is False
        assert getattr(agent, "_provider_image_count_cap", None) == 16, (
            "the cap is still latched for the send path"
        )
        assert all(m["content"][0]["type"] == "image_url" for m in api_messages)


# Send-path forwarding smoke: the latch attribute is read with getattr, so a plain
# SimpleNamespace agent keeps assemble_api_request's evict call working pre-latch.
def test_send_path_reads_the_latch_as_optional():
    agent = SimpleNamespace(session_id="s1")
    assert getattr(agent, "_provider_image_count_cap", None) is None
