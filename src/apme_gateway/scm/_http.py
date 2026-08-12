"""Shared HTTP/TLS helpers for SCM providers (ADR-050)."""

from __future__ import annotations

import os
import ssl

import httpx


def custom_ca_bundle() -> str:
    """Return the configured custom CA bundle path, if any.

    Returns:
        Absolute CA bundle path when configured, else an empty string.
    """
    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        candidate = os.environ.get(key, "").strip()
        if candidate:
            return candidate
    return ""


def http_verify() -> ssl.SSLContext | bool:
    """Return TLS verification settings for outbound HTTPS.

    The gateway may run behind a corporate TLS intercept or use an internal CA.
    ``httpx`` is given the resolved bundle path explicitly so SCM API calls
    trust both the platform store and the custom corporate root.

    Returns:
        SSL context with system roots plus the configured custom bundle, else
        ``True`` for the default platform trust store.
    """
    custom_bundle = custom_ca_bundle()
    if not custom_bundle:
        return True

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    context.load_verify_locations(cafile=custom_bundle)
    return context


def async_client(*, timeout: float) -> httpx.AsyncClient:
    """Build an HTTP client with the configured CA bundle.

    Args:
        timeout: Request timeout in seconds.

    Returns:
        Configured ``httpx.AsyncClient`` instance.
    """
    return httpx.AsyncClient(timeout=timeout, verify=http_verify())
