"""handle_content_policy_refusal: an Anthropic refusal with an empty body still tells the user why."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent.turn_retry_state import TurnRetryState
from agent.turn_truncation import handle_content_policy_refusal


def _agent():
    import agent.transports.anthropic
    from agent.transports import get_transport

    agent = MagicMock()
    agent.api_mode, agent.provider, agent.model, agent.log_prefix = "anthropic_messages", "anthropic", "claude", ""
    agent._is_anthropic_oauth = False
    agent._get_transport.return_value = get_transport("anthropic_messages")
    agent._has_pending_fallback.return_value = False
    agent._try_activate_fallback.return_value = False
    agent._extract_reasoning.return_value = ""
    return agent


def test_empty_refusal_reports_stop_details_explanation():
    response = SimpleNamespace(
        content=[], stop_reason="refusal", usage=None, model="claude",
        stop_details={"type": "refusal", "category": "general_harms", "explanation": "classifier halt"},
    )
    verdict = handle_content_policy_refusal(
        _agent(), response, TurnRetryState(), thinking_spinner=None, messages=[], api_messages=[], api_kwargs={},
        active_system_prompt=None, conversation_history=[], api_call_count=1, effective_task_id="t", turn_id="u",
        api_request_id="r", api_start_time=0.0, retry_count=0, max_retries=0,
    )
    assert verdict.action == "return"
    assert "classifier halt" in verdict.result["error"]


def test_stop_details_explanation_beats_summarized_thinking():
    """A classifier halt after a thinking block leaves content empty but reasoning populated;
    the structured stop_details reason is the refusal reason, not the thinking summary (#142213)."""
    agent = _agent()
    agent._extract_reasoning.return_value = "The reset reply would include session info that's too verbose"
    response = SimpleNamespace(
        content=[], stop_reason="refusal", usage=None, model="claude",
        stop_details={"type": "refusal", "category": "cyber",
                      "explanation": "This request triggered restrictions on violative cyber content."},
    )
    verdict = handle_content_policy_refusal(
        agent, response, TurnRetryState(), thinking_spinner=None, messages=[], api_messages=[], api_kwargs={},
        active_system_prompt=None, conversation_history=[], api_call_count=1, effective_task_id="t", turn_id="u",
        api_request_id="r", api_start_time=0.0, retry_count=0, max_retries=0,
    )
    assert verdict.action == "return"
    assert "violative cyber content" in verdict.result["error"]
    assert "session info" not in verdict.result["error"]


def test_category_only_stop_details_beats_summarized_thinking():
    agent = _agent()
    agent._extract_reasoning.return_value = "I'm thinking about how to phrase this next step"
    response = SimpleNamespace(
        content=[], stop_reason="refusal", usage=None, model="claude",
        stop_details={"type": "refusal", "category": "cyber"},
    )
    verdict = handle_content_policy_refusal(
        agent, response, TurnRetryState(), thinking_spinner=None, messages=[], api_messages=[], api_kwargs={},
        active_system_prompt=None, conversation_history=[], api_call_count=1, effective_task_id="t", turn_id="u",
        api_request_id="r", api_start_time=0.0, retry_count=0, max_retries=0,
    )
    assert verdict.action == "return"
    assert "provider refusal category: cyber" in verdict.result["error"]
    assert "phrase this next step" not in verdict.result["error"]


def test_thinking_summary_still_serves_without_stop_details():
    """No structured reason on the response: the thinking summary remains the fallback text (#113689)."""
    response = SimpleNamespace(content=[], stop_reason="refusal", usage=None, model="claude")
    agent = _agent()
    agent._extract_reasoning.return_value = "I was planning the pairing store changes"
    verdict = handle_content_policy_refusal(
        agent, response, TurnRetryState(), thinking_spinner=None, messages=[], api_messages=[], api_kwargs={},
        active_system_prompt=None, conversation_history=[], api_call_count=1, effective_task_id="t", turn_id="u",
        api_request_id="r", api_start_time=0.0, retry_count=0, max_retries=0,
    )
    assert verdict.action == "return"
    assert "pairing store" in verdict.result["error"]
