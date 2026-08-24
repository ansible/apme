"""Tests for audit metadata serialization and redaction."""

from __future__ import annotations

import json

from apme_engine.graph.audit_metadata import (
    build_audit_metadata_blob,
    sanitize_audit_metadata_value,
    serialize_audit_metadata_value,
)
from apme_engine.graph.sensitivity import redact_url_userinfo


def test_redact_url_userinfo_strips_credentials() -> None:
    """URLs with embedded credentials are redacted."""
    raw = "https://user:secret@example.com/path"
    assert redact_url_userinfo(raw) == "https://[REDACTED]@example.com/path"


def test_build_audit_metadata_blob_decodes_proto_strings() -> None:
    """Gateway persistence decodes per-key JSON metadata once."""
    payload = [{"name": "x", "source": "play"}]
    blob = build_audit_metadata_blob({"variables_used": json.dumps(payload)})
    parsed = json.loads(blob)
    assert parsed["variables_used"] == payload


def test_build_audit_metadata_blob_resanitizes_cleartext_values() -> None:
    """Gateway persistence re-sanitizes decoded audit payloads before storage."""
    payload = [{"name": "db_password", "value": "s3cret", "source": "play"}]
    blob = build_audit_metadata_blob({"variable_set": json.dumps(payload)})
    parsed = json.loads(blob)
    assert parsed["variable_set"][0]["name"] == "[REDACTED]"
    assert parsed["variable_set"][0]["value"] == "[REDACTED]"


def test_sanitize_inbound_src_redacts_url_credentials() -> None:
    """Inbound audit URLs are scrubbed before serialization."""
    sanitized = sanitize_audit_metadata_value(
        "inbound_src",
        ["https://admin:pass@host/file.tar.gz"],
    )
    assert sanitized == ["https://[REDACTED]@host/file.tar.gz"]
    encoded = serialize_audit_metadata_value(sanitized, key="inbound_src")
    assert encoded is not None
    assert json.loads(encoded) == ["https://[REDACTED]@host/file.tar.gz"]


def test_sanitize_variable_set_redacts_nested_dict_secrets() -> None:
    """Nested dict values under benign keys are redacted in variable_set."""
    sanitized = sanitize_audit_metadata_value(
        "variable_set",
        [{"name": "config", "value": {"db_password": "s3cret"}, "source": "play"}],
    )
    assert isinstance(sanitized, list)
    entry = sanitized[0]
    assert isinstance(entry, dict)
    nested = entry["value"]
    assert isinstance(nested, dict)
    assert nested["db_password"] == "[REDACTED]"
