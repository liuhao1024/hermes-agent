"""model_routes alias admission threading (#144082).

Admission (``session_api_turn.admit_api_steps``) re-derives a credentialed route from its
alias to strip the server-side api_key before the settings are persisted. The OpenAI
compatible handlers resolve the route from the request's ``model`` alias but only fill
``requested_model`` when ``direct_model_requests`` is on, so the re-derivation looked up
``None`` and every alias request failed with ``permission_denied``.
"""

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter


def _make_routing_adapter(routes) -> APIServerAdapter:
    """Create an adapter with model_routes configured."""
    config = PlatformConfig(enabled=True, extra={"model_routes": routes})
    return APIServerAdapter(config)


class TestRouteAliasAdmission:

    def test_route_alias_threads_requested_model_without_direct_model_requests(self):
        """A model_routes alias must reach the admission settings even with
        direct_model_requests off (#144082): a missing alias made every alias
        request with a credentialed route fail with permission_denied."""
        route_key = "sk-route" "-secret"
        adapter = _make_routing_adapter(
            {"my-alias": {"model": "openai/gpt-5", "base_url": "http://llm:8080/v1",
                          "api_key": route_key}})
        assert adapter._direct_model_requests is False
        route, overrides, err = adapter._select_request_route(
            {"model": "my-alias"}, session_id="s1", gateway_session_key=None,
            model_alias="my-alias")
        assert err is None
        assert route is not None and route["api_key"] == route_key
        # The exact re-derivation admit_api_steps performs (guard must pass):
        assert adapter._model_routes.get(overrides.get("requested_model")) == route

    def test_route_alias_does_not_shadow_explicit_requested_model(self):
        """With direct_model_requests on, a client-declared bare model keeps its
        place in the overrides; the alias only fills the gap."""
        adapter = _make_routing_adapter(
            {"my-alias": {"model": "openai/gpt-5", "provider": "openrouter"}})
        adapter._direct_model_requests = True
        _, overrides, err = adapter._select_request_route(
            {"model": "my-alias", "provider": "openrouter"}, session_id="s1",
            gateway_session_key=None, model_alias="my-alias")
        assert err is None
        assert overrides.get("requested_model") == "my-alias"
        assert overrides.get("requested_provider") == "openrouter"
