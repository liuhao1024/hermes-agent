"""Free-form handling of MCP tool input schemas: which object nodes may or may not be
stamped with an empty ``properties`` map (#143538)."""

from tools.mcp_tool_schema import _normalize_mcp_input_schema
from tools.schema_sanitizer import sanitize_tool_schemas


def _fastmcp_call_tool_schema():
    """The shape FastMCP meta-tools advertise (issue #143538): ``arguments`` is an open
    object — ``additionalProperties: true`` — wrapped in a nullable anyOf."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "arguments": {
                "anyOf": [{"type": "object", "additionalProperties": True},
                          {"type": "null"}],
                "default": None,
                "description": "Arguments to pass to the tool",
            },
        },
    }


def test_open_nested_object_is_not_stamped_with_empty_properties():
    """An OPEN nested object (``additionalProperties`` absent or not ``False``) keeps its
    free-form meaning: an injected ``properties: {}`` reads as "no fields allowed" to
    strict models, which then send ``{}`` for it. Regression (#143538) — FastMCP
    meta-tools like ``call_tool(name, arguments: dict | None)`` became unusable this
    way. The parameters ROOT and closed objects keep the empty-``properties`` repair.
    """
    schema = _normalize_mcp_input_schema(_fastmcp_call_tool_schema())

    arguments = schema["properties"]["arguments"]
    assert arguments["type"] == "object"
    assert arguments["nullable"] is True
    assert "properties" not in arguments
    assert arguments["required"] == []

    closed = _normalize_mcp_input_schema({
        "type": "object",
        "properties": {"box": {"type": "object", "additionalProperties": False}},
    })
    assert closed["properties"]["box"]["properties"] == {}

    root_only = _normalize_mcp_input_schema({"type": "object", "additionalProperties": True})
    assert root_only["properties"] == {}


def test_freeform_arguments_survive_per_request_sanitization():
    """The per-request sanitization pass must not re-stamp what registration left
    free-form: the model-facing schema keeps no ``properties`` on ``arguments`` while
    the parameters ROOT keeps its guaranteed empty map."""
    registered = _normalize_mcp_input_schema(_fastmcp_call_tool_schema())
    sent = sanitize_tool_schemas([{"type": "function", "function": {
        "name": "mcp__demo__call_tool",
        "description": "Call a tool by name with the given arguments.",
        "parameters": registered}}])[0]

    params = sent["function"]["parameters"]
    arguments = params["properties"]["arguments"]
    assert arguments["type"] == "object"
    assert arguments["additionalProperties"] is True
    assert "properties" not in arguments
    assert arguments["required"] == []
    assert params["type"] == "object"
    assert isinstance(params["properties"], dict)
    assert params["required"] == ["name"]
