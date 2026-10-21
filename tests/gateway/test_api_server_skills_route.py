"""Tests for the api_server /v1/skills route.

Lives in a sibling file because tests/gateway/test_api_server.py is already over the
FILE_LINES target — new route tests grow here instead.
"""

from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter


def _make_adapter() -> APIServerAdapter:
    return APIServerAdapter(PlatformConfig(enabled=True, extra={}))


def _create_skills_app(adapter: APIServerAdapter) -> web.Application:
    """Minimal aiohttp app exposing only the route under test."""
    app = web.Application()
    app["api_server_adapter"] = adapter
    app.router.add_get("/v1/skills", adapter._handle_skills)
    return app


class TestSkillsEndpoint:
    @pytest.mark.asyncio
    async def test_skills_calls_find_all_skills_with_current_signature(self):
        """The handler may only pass kwargs the real find_all_skills() signature accepts —
        an orphaned include_editorial kwarg (left behind by the Collective Wisdom revert,
        see #142575) raised TypeError inside the handler and turned every GET /v1/skills
        into a 500. autospec pins the call shape to the live signature, which a plain
        return_value mock cannot do."""
        fake_skills = [
            {"name": "demo", "description": "Demo skill", "category": "demo"},
        ]
        with patch(
            "tools.skills_tool.find_all_skills",
            autospec=True,
            return_value=list(fake_skills),
        ) as find_all:
            app = _create_skills_app(_make_adapter())
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.get("/v1/skills")
                assert resp.status == 200
                data = await resp.json()
                assert data["object"] == "list"
                assert [s["name"] for s in data["data"]] == ["demo"]
            find_all.assert_called_once()
