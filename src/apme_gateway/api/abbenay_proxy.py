"""HTTP reverse-proxy of in-pod Abbenay admin API (ADR-070).

Maps a **small allowlist** of Gateway ``/api/v1/ai/...`` routes to Abbenay
``/api/...`` on localhost. Injects Abbenay's HTTP Bearer token; does not pass
through caller ``Authorization``. Inference (``GET /api/v1/ai/models`` and
Abbenay chat) is **not** proxied — chat stays Primary → Abbenay gRPC.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Final
from urllib.parse import unquote, urlparse

import httpx
from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

_HTTP_URL_ENV: Final = "APME_ABBENAY_HTTP_URL"
_HTTP_URL_DEFAULT: Final = "http://127.0.0.1:8787"
_HTTP_TOKEN_ENV: Final = "APME_ABBENAY_HTTP_TOKEN"
_GRPC_TOKEN_ENV: Final = "APME_ABBENAY_TOKEN"
_ABBENAY_API_TOKEN_ENV: Final = "ABBENAY_API_TOKEN"
_PROXY_TIMEOUT_S: Final = 60.0

# Hop-by-hop + sensitive headers must not be forwarded (RFC 7230 + ADR-070).
_REQUEST_DROP: Final = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
        "authorization",
        "cookie",
    }
)
_RESPONSE_DROP: Final = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
        "content-encoding",  # httpx may already decompress the body
        "set-cookie",
    }
)

# Admin-only path allowlist (decoded, no leading slash). Matches ADR-070 /
# ABBENAY_AI.md — no chat/sessions/secrets/engines/templates.
_GET_PATHS: Final = frozenset({"config", "providers"})
_GET_PROVIDER_RE: Final = re.compile(r"^provider/[^/]+$")
_POST_PATHS: Final = frozenset({"config"})
_POST_CONFIGURE_RE: Final = re.compile(r"^provider/[^/]+/configure$")
_DELETE_PROVIDER_RE: Final = re.compile(r"^provider/[^/]+$")

router = APIRouter(prefix="/api/v1", tags=["abbenay-admin"])


def abbenay_http_base_url() -> str:
    """Return the Abbenay HTTP base URL (no trailing slash).

    Returns:
        Configured base URL, or ``http://127.0.0.1:8787`` when unset.
    """
    return os.environ.get(_HTTP_URL_ENV, "").strip().rstrip("/") or _HTTP_URL_DEFAULT


def abbenay_http_token() -> str:
    """Return the Bearer token for Abbenay HTTP admin.

    Returns:
        Token from ``APME_ABBENAY_HTTP_TOKEN``, ``APME_ABBENAY_TOKEN``, or
        ``ABBENAY_API_TOKEN``; empty string if none are set.
    """
    for key in (_HTTP_TOKEN_ENV, _GRPC_TOKEN_ENV, _ABBENAY_API_TOKEN_ENV):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def _decode_admin_path(path: str) -> str | None:
    """Decode and validate the path suffix; reject traversal and models.

    Args:
        path: Raw path under ``/api/v1/ai/``.

    Returns:
        Normalized path without a leading slash, or ``None`` if rejected.
    """
    decoded = unquote(path).strip().strip("/")
    if not decoded:
        return None
    # Reject encoded query/fragment delimiters smuggled into the path param.
    if "?" in decoded or "#" in decoded:
        return None
    parts = decoded.split("/")
    if any(part == ".." or part == "." for part in parts):
        return None
    if parts[0] == "models":
        return None
    return decoded


def _method_allows_path(method: str, admin_path: str) -> bool:
    """Return whether ``method`` may proxy ``admin_path``.

    Args:
        method: HTTP method (uppercase).
        admin_path: Normalized allowlist candidate.

    Returns:
        True when the method/path pair is an allowed Abbenay admin route.
    """
    if method == "GET":
        return admin_path in _GET_PATHS or bool(_GET_PROVIDER_RE.fullmatch(admin_path))
    if method == "POST":
        return admin_path in _POST_PATHS or bool(_POST_CONFIGURE_RE.fullmatch(admin_path))
    if method == "DELETE":
        return bool(_DELETE_PROVIDER_RE.fullmatch(admin_path))
    return False


def _upstream_url(admin_path: str, query: str) -> str | None:
    """Build a safe Abbenay upstream URL under ``/api/``.

    Args:
        admin_path: Normalized allowlisted path.
        query: Raw query string (without ``?``).

    Returns:
        Full upstream URL, or ``None`` if the resolved path leaves ``/api/``.
    """
    base = abbenay_http_base_url()
    url = f"{base}/api/{admin_path}"
    if query:
        url = f"{url}?{query}"
    parsed = urlparse(url)
    # Reject escapes that leave /api/ after normalization (e.g. encoded ..).
    path = unquote(parsed.path)
    if not path.startswith("/api/"):
        return None
    if ".." in path.split("/"):
        return None
    return url


def _filter_request_headers(request: Request) -> dict[str, str]:
    """Copy request headers for upstream, stripping auth/cookies/hop-by-hop.

    Args:
        request: Incoming Gateway request.

    Returns:
        Headers to send to Abbenay, with Bearer token injected when configured.
    """
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in _REQUEST_DROP:
            continue
        headers[key] = value
    token = abbenay_http_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _filter_response_headers(upstream: httpx.Response) -> dict[str, str]:
    """Copy upstream response headers, omitting hop-by-hop and Set-Cookie.

    Args:
        upstream: Response from Abbenay HTTP.

    Returns:
        Headers safe to return to the Gateway client.
    """
    return {key: value for key, value in upstream.headers.items() if key.lower() not in _RESPONSE_DROP}


@router.api_route(  # type: ignore[untyped-decorator]
    "/ai/{path:path}",
    methods=["GET", "POST", "DELETE"],
    # Transparent proxy — Abbenay owns the admin schema (ADR-070).
    # Omit HEAD: Starlette derives it from GET without a response body.
    include_in_schema=False,
)
async def proxy_abbenay_admin(path: str, request: Request) -> Response:
    """Reverse-proxy allowlisted Abbenay HTTP admin under ``/api/v1/ai/*``.

    Allowed (examples): ``GET/POST /ai/config``, ``GET /ai/providers``,
    ``POST /ai/provider/{id}/configure``, ``DELETE /ai/provider/{id}``.
    ``GET /api/v1/ai/models`` remains on the main router (Primary). Chat and
    other Abbenay surfaces are not proxied.

    Args:
        path: Path suffix after ``/api/v1/ai/``.
        request: Incoming Gateway request.

    Returns:
        Upstream status/headers/body, or 4xx/502 for reject/unreachable.
    """
    admin_path = _decode_admin_path(path)
    if admin_path is None or not _method_allows_path(request.method.upper(), admin_path):
        return Response(
            content=b'{"detail":"Abbenay admin path not allowed"}',
            status_code=404,
            media_type="application/json",
        )

    if not abbenay_http_token():
        return Response(
            content=b'{"detail":"Abbenay HTTP admin token not configured"}',
            status_code=503,
            media_type="application/json",
        )

    url = _upstream_url(admin_path, request.url.query)
    if url is None:
        return Response(
            content=b'{"detail":"Invalid Abbenay admin path"}',
            status_code=400,
            media_type="application/json",
        )

    headers = _filter_request_headers(request)
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=_PROXY_TIMEOUT_S) as client:
            upstream = await client.request(
                request.method,
                url,
                headers=headers,
                content=body if body else None,
            )
    except httpx.RequestError:
        # Log path only — avoid query strings that may contain secrets.
        logger.warning(
            "Abbenay admin proxy failed contacting %s/api/%s",
            abbenay_http_base_url(),
            admin_path,
            exc_info=True,
        )
        return Response(
            content=b'{"detail":"Abbenay HTTP admin unreachable"}',
            status_code=502,
            media_type="application/json",
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filter_response_headers(upstream),
        media_type=upstream.headers.get("content-type"),
    )
