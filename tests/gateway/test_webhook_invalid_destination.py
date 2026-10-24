"""A permanent destination-config refusal must not advertise itself as retryable (#143793).

``_admit_agent_run`` used to collapse every admission exception into the retryable 503
"Admission unavailable; retry this delivery" — including ``RuntimeStoreError('invalid_params')``
from ``validate_destination``, the answer to a route whose ``deliver`` value has the
``platform:chat_id`` shape the store refuses on every retry. A correctly-implemented client
then retried the same request forever. That refusal is now answered 422, naming the
subscription as what has to change, while refusals that may be startup or serving-state
transients (``not_found``) and unclassified failures keep the retryable 503.
"""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.webhook import WebhookAdapter, _INSECURE_NO_AUTH


def _make_adapter():
    return WebhookAdapter(PlatformConfig(enabled=True, extra={
        "host": "127.0.0.1", "port": 0,
        "routes": {"demo": {"secret": _INSECURE_NO_AUTH, "prompt": "{text}",
                            "deliver": "telegram:12345"}}}))


def _create_app(adapter: WebhookAdapter) -> web.Application:
    app = web.Application(client_max_size=adapter._max_body_bytes)
    from tests.gateway.fixtures.webhook_route_authority import mount_authority
    mount_authority(app, adapter)
    app.router.add_post("/webhooks/{route_name}", adapter._handle_webhook)
    return app


async def _post(cli, delivery_id):
    return await cli.post("/webhooks/demo", json={"hello": "world"},
                          headers={"X-GitHub-Delivery": delivery_id, "X-GitHub-Event": "push"})


def _refuse_with(reason):
    from hermes_state_runtime import RuntimeStoreError

    async def _refuse(_adapter, _event):
        raise RuntimeStoreError(reason)

    return _refuse


@pytest.mark.asyncio
async def test_invalid_destination_refusal_answers_422_not_retryable_503(monkeypatch):
    from gateway.platforms import webhook_ingress

    monkeypatch.setattr(webhook_ingress, "admit_producer", _refuse_with('invalid_params'))
    adapter = _make_adapter()
    async with TestClient(TestServer(_create_app(adapter))) as cli:
        resp = await _post(cli, "perm-1")
        body = await resp.text()
        assert resp.status == 422, (resp.status, body)
        assert "retry" not in body.lower()
        assert "misconfigured" in body


@pytest.mark.asyncio
async def test_transient_store_refusals_keep_the_retryable_503(monkeypatch):
    from gateway.platforms import webhook_ingress

    monkeypatch.setattr(webhook_ingress, "admit_producer", _refuse_with('not_found'))
    adapter = _make_adapter()
    async with TestClient(TestServer(_create_app(adapter))) as cli:
        resp = await _post(cli, "transient-1")
        body = await resp.text()
        assert resp.status == 503, (resp.status, body)
        assert "retry" in body.lower()


@pytest.mark.asyncio
async def test_unclassified_admission_failures_keep_the_retryable_503(monkeypatch):
    from gateway.platforms import webhook_ingress

    async def _boom(_adapter, _event):
        raise RuntimeError("storage is down")

    monkeypatch.setattr(webhook_ingress, "admit_producer", _boom)
    adapter = _make_adapter()
    async with TestClient(TestServer(_create_app(adapter))) as cli:
        resp = await _post(cli, "crash-1")
        assert resp.status == 503, (resp.status, await resp.text())


def test_platform_chat_id_deliver_shape_is_refused_for_good():
    """The reported trigger: a ``deliver`` value in ``platform:chat_id`` shape lands in
    ``validate_destination``, which refuses it as ``invalid_params`` on every retry."""
    from gateway.platforms.webhook_delivery import validate_destination
    from hermes_state_runtime import RuntimeStoreError

    with pytest.raises(RuntimeStoreError) as caught:
        validate_destination({"deliver": "telegram:12345", "deliver_extra": {}})
    assert caught.value.reason == 'invalid_params'


@pytest.mark.asyncio
async def test_coalesced_invalid_destination_releases_delivery_ids(monkeypatch):
    """The coalesced ack (202) went out before admission, so its only remediation is releasing
    the batch's delivery ids for a resend — the misconfigured destination too, or a replay of
    one of those ids would be answered ``duplicate`` by the in-memory guard."""
    from gateway.platforms import webhook_ingress
    from gateway.platforms.webhook import _WebhookDeliveryIdentity

    monkeypatch.setattr(webhook_ingress, "admit_producer", _refuse_with('invalid_params'))
    adapter = _make_adapter()
    profile, route = None, "demo"
    batch = _WebhookDeliveryIdentity.from_parts(profile, route, "batch-1")
    superseded = _WebhookDeliveryIdentity.from_parts(profile, route, "sup-1")
    adapter._seen_deliveries[batch] = 100.0
    adapter._seen_deliveries[superseded] = 100.0
    task = adapter._spawn_agent_run({}, "prompt", "batch-1", 200.0,
                                    route_config={"deliver": "telegram:12345"}, route_name=route,
                                    profile=profile, event_type="push", superseded=("sup-1",))
    await task
    assert batch not in adapter._seen_deliveries
    assert superseded not in adapter._seen_deliveries
