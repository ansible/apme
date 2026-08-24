"""Shared audit-metadata keys and JSON serialization for violation payloads."""

from __future__ import annotations

import json
import logging

from apme_engine.graph.sensitivity import (
    REDACTED,
    redact_sensitive_structure,
    redact_url_userinfo,
    value_looks_sensitive,
    var_looks_sensitive,
)

logger = logging.getLogger(__name__)

AUDIT_JSON_METADATA_KEYS: frozenset[str] = frozenset({"variables_used", "variable_set", "inbound_src"})


def _sanitize_scalar(key: str, value: object) -> object:
    if key == "inbound_src" and isinstance(value, str):
        return redact_url_userinfo(value)
    if isinstance(value, str) and value_looks_sensitive(value):
        return REDACTED
    return value


def _sanitize_audit_structure(key: str, value: object) -> object:
    """Recursively redact sensitive audit payload values before JSON encoding.

    Args:
        key: Metadata key name.
        value: Native Python structure from a GraphRule detail dict.

    Returns:
        Redacted structure safe for persistence.
    """
    if key == "inbound_src":
        if isinstance(value, list):
            return [_sanitize_scalar(key, item) for item in value]
        return _sanitize_scalar(key, value)

    if key == "variables_used" and isinstance(value, list):
        sanitized: list[object] = []
        for entry in value:
            if not isinstance(entry, dict):
                sanitized.append(entry)
                continue
            name = entry.get("name")
            if isinstance(name, str) and var_looks_sensitive(name):
                sanitized.append({**entry, "name": REDACTED})
            else:
                sanitized.append(entry)
        return sanitized

    if key == "variable_set" and isinstance(value, list):
        sanitized_vars: list[object] = []
        for entry in value:
            if not isinstance(entry, dict):
                sanitized_vars.append(entry)
                continue
            patched = dict(entry)
            name = patched.get("name")
            if isinstance(name, str) and var_looks_sensitive(name):
                patched["name"] = REDACTED
            val = patched.get("value")
            if isinstance(val, (dict, list)):
                patched["value"] = redact_sensitive_structure(val)
            elif val is not None and (
                (var_looks_sensitive(str(name)) if name is not None else False) or value_looks_sensitive(val)
            ):
                patched["value"] = REDACTED
            sanitized_vars.append(patched)
        return sanitized_vars

    if isinstance(value, dict):
        return redact_sensitive_structure(value)
    if isinstance(value, list):
        return redact_sensitive_structure(value)
    return _sanitize_scalar(key, value)


def sanitize_audit_metadata_value(key: str, val: object) -> object:
    """Apply key-aware redaction to an audit metadata structure.

    Args:
        key: Metadata key name (``variables_used``, ``variable_set``, ``inbound_src``).
        val: Native Python structure from a GraphRule detail dict.

    Returns:
        Redacted structure safe for persistence.
    """
    return _sanitize_audit_structure(key, val)


def parse_audit_metadata_value(raw: str) -> object:
    """Decode a proto-stored audit metadata JSON string.

    Args:
        raw: JSON string from violation proto metadata.

    Returns:
        Parsed Python object, or the original string when not valid JSON.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def serialize_audit_metadata_value(val: object, *, rule_id: str = "", key: str = "") -> str | None:
    """Serialize an audit metadata value to a JSON string for proto storage.

    Args:
        val: Structure to serialize (list, dict, etc.).
        rule_id: Rule ID for error logging (optional).
        key: Metadata key name for error logging (optional).

    Returns:
        JSON string, or None if serialization fails.
    """
    sanitized = sanitize_audit_metadata_value(key, val) if key in AUDIT_JSON_METADATA_KEYS else val
    try:
        return json.dumps(sanitized, default=str, sort_keys=True)
    except (TypeError, ValueError) as err:
        logger.warning(
            "Failed to serialize audit metadata %s for rule %s: %s",
            key or "?",
            rule_id or "?",
            err,
        )
        return None


def decode_audit_payload_entry(raw: object) -> object:
    """Decode one audit metadata entry that may already be JSON-encoded.

    Args:
        raw: Value from proto metadata or a nested audit payload dict.

    Returns:
        Parsed structure when ``raw`` is a JSON string; otherwise ``raw``.
    """
    if isinstance(raw, str):
        return parse_audit_metadata_value(raw)
    return raw


def build_audit_metadata_blob(metadata: dict[str, str]) -> str:
    """Build a single JSON blob for Gateway persistence from proto metadata.

    Args:
        metadata: Violation proto metadata map.

    Returns:
        JSON object string with decoded audit keys, or empty string when absent.
    """
    audit_payload: dict[str, object] = {}
    for key in AUDIT_JSON_METADATA_KEYS:
        if key not in metadata:
            continue
        decoded = decode_audit_payload_entry(metadata[key])
        audit_payload[key] = sanitize_audit_metadata_value(key, decoded)
    if not audit_payload:
        return ""
    try:
        return json.dumps(audit_payload, default=str, sort_keys=True)
    except (TypeError, ValueError) as err:
        logger.warning("Failed to build Gateway audit blob: %s", err)
        return ""
