"""Google Gemini provider profiles.

gemini:            Google AI Studio (API key) — uses GeminiNativeClient

Reports api_mode="chat_completions" but uses a custom native client
that bypasses the standard OpenAI transport. The profile captures auth
and endpoint metadata for auth.py / runtime_provider.py migration, and
carries the thinking_config translation hook so the transport's profile
path produces the same extra_body shape the legacy flag path did.
"""

import logging
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile, _profile_user_agent

logger = logging.getLogger(__name__)


class GeminiProfile(ProviderProfile):
    """Gemini — translate reasoning_config to thinking_config in extra_body."""

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """Fetch live model list from Gemini's native /v1beta/models endpoint.

        Gemini's native API requires the API key as a query parameter (?key=...),
        not as a Bearer token. The response uses "name" fields like "models/gemini-2.5-pro",
        which we strip to produce "gemini-2.5-pro" IDs.

        Returns None on failure, so callers fall back to the static fallback_models list.
        """
        if not api_key:
            return None

        effective_base = base_url or self.base_url
        if not effective_base:
            return None

        # Use native /v1beta/models endpoint with query-param auth
        url = effective_base.rstrip("/") + "/models?key=" + api_key

        import json
        import urllib.request

        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", _profile_user_agent())

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            # Native Gemini API returns {"models": [{"name": "models/gemini-2.5-pro", ...}, ...]}
            # Strip the "models/" prefix to produce the IDs Hermes expects
            models_list = data if isinstance(data, list) else data.get("models", [])
            model_ids = []
            prefix_len = len("models/")
            for m in models_list:
                if isinstance(m, dict) and "name" in m:
                    name = m["name"]
                    if name.startswith("models/"):
                        model_ids.append(name[prefix_len:])
                    else:
                        model_ids.append(name)
            return model_ids
        except Exception as exc:
            logger.debug("fetch_models(%s): %s", self.name, exc)
            return None

    def build_extra_body(
        self, *, session_id: str | None = None, **context: Any
    ) -> dict[str, Any]:
        """Emit extra_body.thinking_config (native) or extra_body.extra_body.google.thinking_config
        (OpenAI-compat /openai subpath), mirroring the legacy path's behavior.
        """
        from agent.transports.chat_completions import (
            _build_gemini_thinking_config,
            _is_gemini_openai_compat_base_url,
            _snake_case_gemini_thinking_config,
        )

        model = context.get("model") or ""
        reasoning_config = context.get("reasoning_config")
        base_url = context.get("base_url") or self.base_url

        raw_thinking_config = _build_gemini_thinking_config(model, reasoning_config)
        if not raw_thinking_config:
            return {}

        body: dict[str, Any] = {}
        if self.name == "gemini" and _is_gemini_openai_compat_base_url(base_url):
            thinking_config = _snake_case_gemini_thinking_config(raw_thinking_config)
            if thinking_config:
                body["extra_body"] = {"google": {"thinking_config": thinking_config}}
        else:
            body["thinking_config"] = raw_thinking_config
        return body


gemini = GeminiProfile(
    name="gemini",
    aliases=("google", "google-gemini", "google-ai-studio"),
    api_mode="chat_completions",
    env_vars=("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    base_url="https://generativelanguage.googleapis.com/v1beta",
    auth_type="api_key",
    default_aux_model="gemini-3.5-flash",
)

register_provider(gemini)
