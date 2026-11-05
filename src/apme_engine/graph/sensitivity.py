"""Shared sensitivity and redaction helpers used across engine components."""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_SENSITIVE_WORDS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "secrets",
        "token",
        "api_key",
        "apikey",
        "credential",
        "credentials",
        "cred",
        "authorization",
        "private_key",
        "ssh_key",
        "access_key",
        "client_key",
    }
)

_PASS_SUFFIX_RE = re.compile(r"(?:^|[_.])pass$")
_SCALAR_SECRET_RE = re.compile(
    r"(?i)("
    r"password\s*[=:]|"
    r"passwd\s*[=:]|"
    r"api[_-]?key\s*[=:]|"
    r"bearer\s+\S+|"
    r"BEGIN\s+(?:[A-Z]+\s+)?PRIVATE\s+KEY|"
    r"://[^:]+:[^@]+@|"
    r"\$ANSIBLE_VAULT;|"
    r"!vault\b"
    r")"
)
_OPAQUE_TOKEN_RE = re.compile(
    r"(?i)(?:"
    r"AKIA[0-9A-Z]{16}|"
    r"sk[_-](?:live|test)[_-]|"
    r"ghp_[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]+|"
    r"eyJ[A-Za-z0-9_-]+\.eyJ"
    r")"
)
_WORD_BOUNDARY_RE = re.compile(r"(?:^|[_.'\"\[])({})(?:[_.'\"\[\]]|$)".format("|".join(_SENSITIVE_WORDS)))
_URL_USERINFO_RE = re.compile(r"^([^:/?#]+)://([^/?#]*)@")
_URL_LITERAL_RE = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]*://"
    r"(?:\{\{(?:(?!\}\})[^\n])*\}\}|\{%[^\n]*?%\}|(?!\{\{|\{%)[^\s\"'<>])+"
)
_SCP_LITERAL_RE = re.compile(
    r"(?<![\w.-])(?:([^@\s\"'<>]+)@)?([^:/\s\"'<>]+):"
    r"((?:\{\{(?:(?!\}\})[^\n])*\}\}|\{%[^\n]*?%\}|(?!\{\{|\{%)[^\s\"'<>])+)"
)
_JINJA_TEMPLATE_RE = re.compile(r"\{\{(?:(?!\}\})[^\n])*\}\}|\{%[^\n]*?%\}")


def redact_url_userinfo(url: str) -> str:
    """Strip credential userinfo from a URL string.

    Args:
        url: URL that may contain ``user:pass@`` before the host.

    Returns:
        URL with userinfo replaced by ``[REDACTED]``.
    """
    match = _URL_USERINFO_RE.match(url)
    if not match:
        return url
    scheme = match.group(1)
    rest = url[match.end() :]
    return f"{scheme}://{REDACTED}@{rest}"


def redact_url_query(url: str) -> str:
    """Replace URL query and fragment contents so tokens are not exposed.

    Args:
        url: URL or source string that may contain a query component.

    Returns:
        The source with its query and fragment replaced by ``[REDACTED]``.
    """
    query_start = url.find("?")
    fragment_start = url.find("#")
    if fragment_start >= 0 and (query_start < 0 or fragment_start < query_start):
        return f"{url[:fragment_start]}#[REDACTED]"
    if query_start < 0:
        return url
    if fragment_start < 0:
        return f"{url[:query_start]}?[REDACTED]"
    return f"{url[:query_start]}?[REDACTED]#[REDACTED]"


def _redact_query_fragment_preserving_templates(value: str) -> str:
    """Redact query and fragment data outside embedded Jinja templates.

    Mask template regions while stripping URL or SCP query and fragment data
    so delimiters inside Jinja cannot truncate its syntax or a path suffix.

    Args:
        value: URL or SCP-style source path to sanitize.

    Returns:
        The value with query and fragment data outside templates redacted.
    """

    def redact_urls_in_template(match: re.Match[str]) -> str:
        template = match.group(0)

        def redact_embedded_url(url_match: re.Match[str]) -> str:
            embedded_url = url_match.group(0)
            # Recursion only follows a URL strictly smaller than its containing
            # template, so nested templates cannot recurse on the same input.
            if len(embedded_url) >= len(template):
                return redact_url_query(redact_url_userinfo(embedded_url))
            return _redact_query_fragment_preserving_templates(redact_url_userinfo(embedded_url))

        return _URL_LITERAL_RE.sub(redact_embedded_url, template)

    # Sanitize URL literals embedded in Jinja before masking the template from
    # the outer URL query pass. Otherwise nested credentials and tokens survive.
    value = _JINJA_TEMPLATE_RE.sub(redact_urls_in_template, value)

    templates: list[str] = []
    markers: list[str] = []

    def mask_template(match: re.Match[str]) -> str:
        template = match.group(0)
        marker = f"\x00APME_JINJA_{len(templates)}\x00"
        while marker in value:
            marker += "_"
        templates.append(template)
        markers.append(marker)
        return marker

    masked_value = _JINJA_TEMPLATE_RE.sub(mask_template, value)
    redacted_value = redact_url_query(masked_value)
    for marker, template in zip(markers, templates, strict=True):
        redacted_value = redacted_value.replace(marker, template)
    return redacted_value


def redact_urls_in_text(value: str) -> str:
    """Redact URL credentials, query, and fragment contents in text.

    Args:
        value: Text that may contain URLs or SCP-style source strings.

    Returns:
        Text with URL credentials, queries, and fragments redacted.
    """
    sanitized = _URL_LITERAL_RE.sub(
        lambda match: _redact_query_fragment_preserving_templates(redact_url_userinfo(match.group(0))),
        value,
    )

    def redact_scp_source(match: re.Match[str]) -> str:
        userinfo, host, path = match.groups()
        safe_source = f"{host}:{_redact_query_fragment_preserving_templates(path)}"
        if userinfo is None:
            return safe_source
        safe_userinfo = "[REDACTED]" if ":" in userinfo else userinfo
        return f"{safe_userinfo}@{safe_source}"

    return _SCP_LITERAL_RE.sub(redact_scp_source, sanitized)


def var_looks_sensitive(var_name: str) -> bool:
    """Check if a variable name matches sensitive patterns.

    Args:
        var_name: Variable name or dotted path to check.

    Returns:
        True if the variable name contains a sensitive word as a segment.
    """
    lower = var_name.lower()
    if _PASS_SUFFIX_RE.search(lower):
        return True
    return bool(_WORD_BOUNDARY_RE.search(lower))


def value_looks_sensitive(value: object) -> bool:
    """Check if a scalar value contains credential-like content.

    Args:
        value: Variable value to inspect.

    Returns:
        True when a string value matches common secret patterns.
    """
    if not isinstance(value, str):
        return False
    return bool(_SCALAR_SECRET_RE.search(value) or _OPAQUE_TOKEN_RE.search(value))


def redact_sensitive_structure(
    value: object,
    *,
    redact_all_scalars: bool = False,
    depth: int = 0,
    max_depth: int = 32,
) -> object:
    """Recursively redact nested structures while preserving overall shape.

    Args:
        value: Arbitrary nested structure.
        redact_all_scalars: When True, replace all scalar leaves with
            ``[REDACTED]``. When False, only redact credential-like values.
        depth: Current recursion depth.
        max_depth: Maximum depth before replacing the subtree wholesale.

    Returns:
        A redacted structure safe for persistence or display.
    """
    if depth > max_depth:
        return REDACTED
    if isinstance(value, dict):
        return {
            k: REDACTED
            if var_looks_sensitive(str(k))
            else redact_sensitive_structure(
                v,
                redact_all_scalars=redact_all_scalars,
                depth=depth + 1,
                max_depth=max_depth,
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            redact_sensitive_structure(
                item,
                redact_all_scalars=redact_all_scalars,
                depth=depth + 1,
                max_depth=max_depth,
            )
            for item in value
        ]
    if isinstance(value, str):
        if redact_all_scalars or value_looks_sensitive(value):
            return REDACTED
        return value
    if redact_all_scalars and value is not None:
        return REDACTED
    return value
