"""A quota-exhaustion AuthError from the provider seam must not read as "not connected".

`_creds_for_switched_provider`'s resolver call is wrapped in a generic except that formats every
failure as a missing credential (#145351). A Codex subscription that is merely out of quota raises
`AuthError(code=codex_rate_limited, relogin_required=False)` with "Credentials are still valid" in
the message — pointing that at `hermes auth add` is wrong on every surface that already classifies
it (format_auth_error, primary_failure_wording).
"""

from hermes_cli.auth_constants import CODEX_RATE_LIMITED_CODE, AuthError
from hermes_cli.model_switch import _Switch, _creds_for_switched_provider


def _switch_on(target_provider: str = "openai-codex") -> _Switch:
    st = _Switch(
        raw_input="gpt-6-luna",
        current_provider="openai",
        current_model="gpt-6-luna",
        current_base_url="",
        current_api_key="",
        is_global=False,
        explicit_provider="",
        user_providers=None,
        custom_providers=None,
        new_model="gpt-6-luna",
        target_provider=target_provider,
        provider_label="ChatGPT or Codex Subscription",
    )

    def resolve_runtime(**kwargs):
        raise AssertionError("resolve_runtime must be replaced per-test")

    st.resolve_runtime = resolve_runtime
    return st


def test_quota_exhaustion_is_relayed_verbatim_without_auth_hint():
    st = _switch_on()

    def resolve_runtime(**kwargs):
        raise AuthError(
            "Codex provider quota exhausted (429); retry after 10124s. Credentials are still valid.",
            provider="openai-codex", code=CODEX_RATE_LIMITED_CODE, relogin_required=False)

    st.resolve_runtime = resolve_runtime
    result = _creds_for_switched_provider(st)

    assert result is not None and not result.success
    assert "quota exhausted (429)" in result.error_message
    assert "retry after 10124s" in result.error_message
    assert "not connected" not in result.error_message
    assert "hermes auth add" not in result.error_message
    assert result.target_provider == "openai-codex"


def test_genuine_missing_credentials_still_get_the_auth_hint():
    st = _switch_on()

    def resolve_runtime(**kwargs):
        raise AuthError(
            "No API key or login found for openai-codex.",
            provider="openai-codex", relogin_required=True)

    st.resolve_runtime = resolve_runtime
    result = _creds_for_switched_provider(st)

    assert result is not None and not result.success
    assert "is not connected" in result.error_message
    assert "hermes auth add openai-codex" in result.error_message


def test_generic_resolver_failure_still_gets_the_auth_hint():
    st = _switch_on()

    def resolve_runtime(**kwargs):
        raise RuntimeError("resolver exploded")

    st.resolve_runtime = resolve_runtime
    result = _creds_for_switched_provider(st)

    assert result is not None and not result.success
    assert "is not connected" in result.error_message
    assert "Details: resolver exploded" in result.error_message
