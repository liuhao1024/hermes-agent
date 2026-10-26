"""``legacy_launch_params`` must not revive a route from a virtual route's settlement stamp.

A MoA turn pins ``base_url: moa://local`` on its override and the usage accounting records that
sentinel as the row's ``billing_base_url``. Lifting it into the launch params froze a URL the
launch check refuses (``must be an http(s) URL``), so every later resume of the chat died as
``invalid_params field=base_url`` (#144984) while the transcript itself was healthy.
"""

from gateway.session_local_migration import legacy_launch_params


def _row(**overrides):
    row = {
        'id': 'session-1', 'source': 'cli', 'model': 'glm-4.7',
        'model_config': None, 'cwd': '/tmp/hermes',
        'billing_provider': 'custom', 'billing_base_url': 'moa://local',
    }
    row.update(overrides)
    return row


def test_moa_sentinel_billing_endpoint_is_not_a_route():
    params, _ = legacy_launch_params(_row())
    assert 'base_url' not in params


def test_moa_sentinel_does_not_imply_a_custom_provider():
    # A bare-custom billing class without a real endpoint carries no route: pairing provider
    # 'custom' with the profile's endpoint would bill the profile's host for the old chat.
    params, _ = legacy_launch_params(_row())
    assert 'provider' not in params


def test_named_provider_route_survives_without_the_sentinel_endpoint():
    # A non-bare virtual provider stamp is a routable identity on its own; it keeps the provider
    # while the sentinel endpoint stays out of the launch.
    params, _ = legacy_launch_params(_row(billing_provider='moa'))
    assert params.get('provider') == 'moa'
    assert 'base_url' not in params


def test_real_billing_endpoint_still_revives_the_route():
    # http(s) settlement endpoints keep the bare-custom heal (#136323) exactly as before.
    params, _ = legacy_launch_params(_row(billing_base_url='https://ollama.com/v1'))
    assert params['base_url'] == 'https://ollama.com/v1'
    assert params['provider'] == 'custom'


def test_gateway_runtime_base_url_wins_over_billing_column():
    # The stored runtime route stays authoritative; the billing column only fills a gap.
    row = _row(model_config={'gateway_runtime': {'provider': 'custom:glm', 'base_url': 'https://glm/v1'}},
               billing_base_url='https://ollama.com/v1')
    params, _ = legacy_launch_params(row)
    assert params['base_url'] == 'https://glm/v1'
    assert params['provider'] == 'custom:glm'
