"""PyPI passthrough for non-collection packages."""

from __future__ import annotations

import base64
import binascii
import hmac
import secrets
from html import escape
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx

DEFAULT_PYPI_URL = "https://pypi.org"
_FILE_HEADERS = ("accept-ranges", "content-encoding", "content-length", "content-range", "content-type")


class _ArtifactLinks(HTMLParser):
    """Rebuild a Simple API page using only locally served distribution links."""

    def __init__(self, passthrough: PyPIPassthrough, base_url: httpx.URL) -> None:
        """Initialize the parser with a function returning local URLs and filenames.

        Args:
            passthrough: Proxy signing the approved artifact links.
            base_url: The index page URL for resolving relative links.
        """
        super().__init__()
        self.passthrough = passthrough
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Preserve Simple API attributes on approved distribution anchors.

        Args:
            tag: Parsed HTML tag.
            attrs: Parsed tag attributes and values.
        """
        if tag != "a":
            return
        values = dict(attrs)
        if (href := values.get("href")) is None:
            return
        try:
            link = self.passthrough._local_artifact_link(self.base_url.join(href))
        except httpx.InvalidURL:
            return
        if link is None:
            return
        url, filename = link
        attributes = "".join(
            f' {name}="{escape(values[name] or "")}"'
            for name in ("data-requires-python", "data-yanked", "data-core-metadata", "data-dist-info-metadata")
            if name in values
        )
        self.links.append(f'<a href="{escape(url)}"{attributes}>{escape(filename)}</a>')


class PyPIPassthrough:
    """Forwards Simple API requests to an upstream PyPI server."""

    def __init__(self, pypi_url: str = DEFAULT_PYPI_URL, timeout: float = 15.0) -> None:
        """Initialise with upstream PyPI base URL.

        Args:
            pypi_url: Base URL of the upstream PyPI-compatible index.
            timeout: HTTP client timeout in seconds.
        """
        base_url = httpx.URL(pypi_url.rstrip("/"))
        self._index_origin = (base_url.scheme, base_url.netloc)
        self._index_auth = httpx.BasicAuth(base_url.username, base_url.password) if base_url.userinfo else None
        self._client = httpx.AsyncClient(
            base_url=base_url.copy_with(username=None, password=None),
            timeout=timeout,
            follow_redirects=True,
        )
        self._artifact_key = secrets.token_bytes(32)
        self._artifact_origins = {self._index_origin}
        if self._index_origin == ("https", b"pypi.org"):
            self._artifact_origins.add(("https", b"files.pythonhosted.org"))

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def fetch_project_page(self, package_name: str, *, local_artifacts: bool = False) -> tuple[str, int]:
        """Fetch a project's Simple API page from upstream PyPI.

        Args:
            package_name: Package name used in the Simple API path.
            local_artifacts: Serve approved wheel links through the pod-local proxy.

        Returns:
            Tuple of (html_content, status_code).
        """
        if local_artifacts and self._index_auth is not None and self._index_origin[0] != "https":
            return "", 502
        resp = await self._client.get(
            f"/simple/{package_name}/",
            headers={"Accept": "text/html"},
            auth=self._index_auth,
            follow_redirects=not (local_artifacts and self._index_auth is not None),
        )
        if not local_artifacts or not resp.is_success:
            return resp.text if not local_artifacts else "", resp.status_code
        parser = _ArtifactLinks(self, resp.url)
        parser.feed(resp.text)
        parser.close()
        return "<!DOCTYPE html><html><body>" + "\n".join(parser.links) + "</body></html>", resp.status_code

    def _allows_artifact(self, url: httpx.URL) -> bool:
        """Check the upstream artifact origin and reject credentials in links.

        Args:
            url: The candidate artifact URL.

        Returns:
            Whether the link uses an approved scheme and origin.
        """
        return (
            url.scheme in ("http", "https")
            and (url.scheme, url.netloc) in self._artifact_origins
            and not (url.userinfo or url.query)
        )

    def _local_artifact_link(self, url: httpx.URL) -> tuple[str, str] | None:
        """Sign the URL of a wheel advertised by the upstream index.

        Args:
            url: Absolute artifact URL advertised by the upstream index.

        Returns:
            Local link and filename, or None if the wheel cannot be relayed.
        """
        filename = PurePosixPath(url.path).name
        if not filename.endswith(".whl") or not self._allows_artifact(url):
            return None
        target = str(url.copy_with(fragment=None)).encode()
        token = base64.urlsafe_b64encode(target).decode().rstrip("=")
        signature = hmac.digest(self._artifact_key, target, "sha256").hex()
        link = f"/python-wheels/{signature}/{token}/{quote(filename, safe='')}"
        if url.fragment:
            link += f"#{url.fragment}"
        return link, filename

    async def open_artifact(
        self, signature: str, token: str, filename: str, method: str, range_header: str | None
    ) -> tuple[httpx.Response, dict[str, str]] | None:
        """Open a streamed request for a wheel previously advertised by this proxy.

        Args:
            signature: Signature for the encoded upstream URL.
            token: Encoded upstream URL.
            filename: Exact wheel basename or its PEP 658 metadata filename.
            method: GET or HEAD.
            range_header: Optional requested byte range.

        Returns:
            Upstream response requiring close, with safe response headers, or None for an invalid link.
        """
        if len(token) > 4096 or len(signature) != 64 or not signature.isascii():
            return None
        try:
            target = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        except (ValueError, binascii.Error):
            return None
        if not hmac.compare_digest(signature, hmac.digest(self._artifact_key, target, "sha256").hex()):
            return None
        url = httpx.URL(target.decode())
        expected = PurePosixPath(url.path).name
        if not self._allows_artifact(url) or filename not in (expected, f"{expected}.metadata"):
            return None
        if filename.endswith(".metadata"):
            url = url.copy_with(raw_path=url.raw_path + b".metadata")
        headers = {"Range": range_header} if range_header else {}
        request = self._client.build_request(method, url, headers=headers)
        auth = self._index_auth if (url.scheme, url.netloc) == self._index_origin else None
        response = await self._client.send(request, auth=auth, stream=True, follow_redirects=False)
        return response, {name: response.headers[name] for name in _FILE_HEADERS if name in response.headers}
