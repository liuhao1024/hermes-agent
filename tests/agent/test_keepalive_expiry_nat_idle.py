"""The keepalive pool's idle window stays at httpx's library default (5s).

The shared LLM transport used a 20s ``keepalive_expiry``. On networks whose
TCP idle timeout is shorter than that (captive-portal NAT reaps flows after
~8s), a pooled socket died inside the window and the next request wrote into
it: BrokenPipe / a 60s wait for the first stream chunk (#146244). 5s —
httpx's own default — still reaps well ahead of the reverse-proxy 30-60s
idle timeouts the window exists to beat.
"""

import httpx

from agent import process_bootstrap
from agent.process_bootstrap import build_keepalive_http_client


def _proxy_free_env(monkeypatch):
    for name in (
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    process_bootstrap.close_shared_transports()

    return monkeypatch


def _mounted(client, scheme="https://"):
    return next(t for pat, t in client._mounts.items() if str(pat.pattern) == scheme)


def _pool_expiry(client, scheme="https://"):
    transport = getattr(_mounted(client, scheme), "_inner", _mounted(client, scheme))
    return transport._pool._keepalive_expiry


def test_keepalive_expiry_is_the_httpx_library_default():
    assert process_bootstrap.KEEPALIVE_EXPIRY_S == httpx.Limits().keepalive_expiry


def test_direct_transport_reaps_idle_sockets_after_5s(monkeypatch):
    _proxy_free_env(monkeypatch)
    client = build_keepalive_http_client("https://api.example.com/v1")
    assert _pool_expiry(client) == process_bootstrap.KEEPALIVE_EXPIRY_S
    client.close()


def test_async_transport_reaps_idle_sockets_after_5s(monkeypatch):
    _proxy_free_env(monkeypatch)
    client = build_keepalive_http_client("https://api.example.com/v1", async_mode=True)
    assert _pool_expiry(client) == process_bootstrap.KEEPALIVE_EXPIRY_S


def test_proxy_backed_client_keeps_the_same_idle_window(monkeypatch):
    _proxy_free_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    client = build_keepalive_http_client("https://api.example.com/v1")
    assert _pool_expiry(client, "all://") == process_bootstrap.KEEPALIVE_EXPIRY_S
