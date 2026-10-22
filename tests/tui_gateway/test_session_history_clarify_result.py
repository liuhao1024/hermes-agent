"""The compact resume projection keeps a clarify tool result (#142811).

``_history_to_messages`` ships a tool row's original ``content`` only for card
renderers that need it. The clarify card is one of them: it re-renders the
answered Q&A from the tool result, so dropping the content collapses an
already-answered card back to an unanswered tool row on every resume/reload.
"""

import json

import tui_gateway.server as srv


def test_history_to_messages_keeps_the_clarify_result_for_resume_display():
    clarify_result = json.dumps({
        "responses": [
            {"question": "Which deployment target?", "status": "answered", "user_response": "staging"}
        ],
        "outcome": "submitted",
    })
    history = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "clarify",
                        "arguments": json.dumps({"questions": [{"question": "Which deployment target?"}]}),
                    },
                },
                {
                    "id": "call_2",
                    "function": {
                        "name": "run_terminal",
                        "arguments": json.dumps({"command": "ls"}),
                    },
                },
            ],
        },
        {"role": "tool", "content": clarify_result, "tool_call_id": "call_1"},
        {"role": "tool", "content": "file list", "tool_call_id": "call_2"},
    ]

    projected = {
        m.get("tool_call_id"): m for m in srv._history_to_messages(history) if m.get("role") == "tool"
    }

    assert projected["call_1"]["content"] == clarify_result
    assert "content" not in projected["call_2"]
