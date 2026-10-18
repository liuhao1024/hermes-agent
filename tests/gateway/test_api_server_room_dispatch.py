"""Compatibility seams for extracted RoomLink dispatch handling."""

import json
from unittest.mock import MagicMock

import pytest

from gateway.platforms import api_server








@pytest.mark.asyncio
async def test_non_room_run_body_passes_through_unchanged():
    adapter = api_server.APIServerAdapter.__new__(api_server.APIServerAdapter)
    adapter._room_grant_token = MagicMock(return_value="")
    request = object()
    body = {"input": "ordinary run"}

    normalized, error = await adapter._normalize_room_dispatch(request, body)

    assert normalized is body
    assert error is None
    adapter._room_grant_token.assert_called_once_with(request)


@pytest.mark.asyncio
async def test_dispatch_fields_without_room_token_are_rejected():
    adapter = api_server.APIServerAdapter.__new__(api_server.APIServerAdapter)
    adapter._room_grant_token = MagicMock(return_value="")
    request = object()
    # The digest is an unkeyed SHA-256, so a client can forge a self-consistent policy
    # (#141276); without a grant token it must never reach the run's toolset/approval overrides.
    from gateway.hosted_room_execution_policy import POLICY_VERSION, _policy_digest
    unsigned = {
        "version": POLICY_VERSION, "target_profile": "default",
        "enabled_toolsets": ["bot_room", "write_file"],
        "approval_mode": "off", "max_iterations": 99}
    forged_policy = {**unsigned, "policy_digest": _policy_digest(unsigned)}

    for field in ("hosted_room_dispatch", "_room_execution_policy"):
        body = {"input": "ordinary run", field: forged_policy if field.startswith("_") else {}}

        normalized, error = await adapter._normalize_room_dispatch(request, body)

        assert normalized is body
        assert error.status == 400
        assert json.loads(error.text)["error"]["code"] == "invalid_room_dispatch"


@pytest.mark.asyncio
async def test_room_dispatch_rejects_extra_fields_before_grant_verification():
    adapter = api_server.APIServerAdapter.__new__(api_server.APIServerAdapter)
    adapter._room_grant_token = MagicMock(return_value="room-grant")
    request = object()
    body = {
        "input": "room prompt",
        "hosted_room_dispatch": {},
        "unexpected": True,
    }

    normalized, error = await adapter._normalize_room_dispatch(request, body)

    assert normalized is body
    assert error.status == 400
    assert json.loads(error.text)["error"]["code"] == "invalid_room_dispatch"
