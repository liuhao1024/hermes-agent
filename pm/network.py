"""Bounded retries for PM-owned, idempotent network operations."""

from __future__ import annotations

import errno
import http.client
import logging
import socket
import ssl
import time
import urllib.error
import urllib.parse
from datetime import timezone, UTC
from email.utils import parsedate_to_datetime
from typing import Callable, TypeVar

_T = TypeVar("_T")
_ATTEMPTS = 4
_MAX_DELAY = 30.0
_HTTP_RETRY = frozenset({408, 429, 500, 502, 503, 504})
_R2_HOSTS = frozenset({"hermes-assets.nousresearch.com"})
_NETWORK_ERRNOS = frozenset({
    errno.ECONNABORTED, errno.ECONNREFUSED, errno.ECONNRESET,
    errno.ENETDOWN, errno.ENETRESET, errno.ENETUNREACH,
    errno.EHOSTUNREACH, errno.EPIPE, errno.ETIMEDOUT,
})


def is_transient(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _HTTP_RETRY
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, Exception) and is_transient(exc.reason)
    if isinstance(exc, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
        return True
    if isinstance(exc, ssl.SSLError):
        return False
    if isinstance(exc, socket.gaierror):
        return exc.errno == socket.EAI_AGAIN
    if isinstance(exc, (TimeoutError, ConnectionError, http.client.IncompleteRead)):
        return True
    return isinstance(exc, OSError) and exc.errno in _NETWORK_ERRNOS


def edge_block(exc: BaseException) -> str | None:
    """Explain a 403/429 that a CDN firewall sent instead of the server, else None.

    A bare "HTTP 403" from a regional block reads as a broken install or a missing
    file (#128295). Cloudflare marks challenges with ``cf-mitigated`` and Vercel's
    firewall sets ``x-vercel-mitigated``. Cloudflare's own block page carries
    neither, but on our R2 custom domain a response without ``cf-cache-status``
    never reached the bucket (Cloudflare R2 troubleshooting docs); other hosts
    behind Cloudflare make no such promise, so that fallback is R2-only.
    """
    if not isinstance(exc, urllib.error.HTTPError) or exc.code not in (403, 429):
        return None
    headers = exc.headers or {}
    host = urllib.parse.urlsplit(exc.filename or "").hostname or ""
    if headers.get("x-vercel-mitigated"):
        cdn = "Vercel"
    elif headers.get("cf-mitigated") or (
            host in _R2_HOSTS and (headers.get("server") or "").lower() == "cloudflare"
            and not headers.get("cf-cache-status")):
        cdn = "Cloudflare"
    else:
        return None
    return (f"{host or 'the server'}'s CDN firewall ({cdn}) refused this network. Your install is fine; "
            "retry from another network")


def _delay(exc: Exception, attempt: int) -> float:
    delay = float(2 ** attempt)
    if isinstance(exc, urllib.error.HTTPError):
        value = exc.headers.get("Retry-After", "") if exc.headers else ""
        if value:
            try:
                retry_after = int(value)
            except ValueError:
                try:
                    when = parsedate_to_datetime(value)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=UTC)
                    retry_after = when.timestamp() - time.time()
                except (TypeError, ValueError, OverflowError):
                    retry_after = 0.0
            delay = max(delay, retry_after)
    return min(delay, _MAX_DELAY)


def retry_network(
    operation: Callable[[], _T], *, wait: Callable[[float], None] | None = None,
) -> _T:
    """Retry the complete request and body read, never an install or publish.

    The caller resets attempt-local buffers and keeps any resumable bytes.
    A download supplies an interruptible wait so pause does not wait for backoff.
    """
    attempt = 1
    while True:
        try:
            return operation()
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
            if not is_transient(exc) or attempt >= _ATTEMPTS:
                raise
            delay = _delay(exc, attempt - 1)
            attempt += 1
            logging.getLogger(__name__).warning(
                "network request failed (%s); retrying in %gs (attempt %d/%d)",
                exc, delay, attempt, _ATTEMPTS,
            )
            (wait or time.sleep)(delay)


def _config_flag(text: str, section: str, key: str) -> bool:
    """Whether top-level ``section:`` sets ``key`` to a truthy scalar.

    PM's bootstrap interpreter runs before any dependency is installed, so no
    YAML library is importable there and this narrow stdlib reader is all that
    can see config.yaml. Only the flat ``section:\\n  key: value`` shape is
    recognized — comments, quoting and the loader's two-space layout are
    handled; flow mappings and anchors are not. Truthiness mirrors what
    ruamel's loader hands hermes_cli (``true``/``yes``/``on``/``1``).
    """
    seen_section = False
    child_indent: int | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if not seen_section:
            seen_section = indent == 0 and stripped == f"{section}:"
            continue
        if indent == 0:
            break  # a sibling top-level key ends the section
        if child_indent is None:
            child_indent = indent
        if indent != child_indent:
            continue  # deeper nesting belongs to another key
        name, sep, value = stripped.partition(":")
        if sep and name.strip() == key:
            return value.strip().strip("'\"").lower() in ("true", "yes", "on", "1")
    return False


def apply_configured_ipv4_preference() -> None:
    """Apply the user's ``network.force_ipv4`` to this process, best-effort.

    hermes_cli applies the same flag through ``apply_ipv4_preference`` before
    its HTTP clients exist, but pm boots first — on a bare bootstrap Python
    where hermes_cli's YAML loader cannot run — so its downloader silently
    ignored the setting: every AAAA record of a dead-IPv6 host burned the
    full 60 s connect timeout, repeated by ``retry_network`` (#144208).
    An unreadable config or absent flag leaves networking untouched.
    """
    try:
        from hermes_constants import apply_ipv4_preference, get_config_path

        text = get_config_path().read_text(encoding="utf-8-sig")
    except (ImportError, OSError):
        return
    try:
        force = _config_flag(text, "network", "force_ipv4")
    except Exception:  # health: allow BLE001 -- best-effort: a malformed flag must never block the bootstrap install
        return
    if force:
        apply_ipv4_preference(force=True)
