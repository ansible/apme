"""Validation and serialization helpers for portal-managed AI providers."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from fastapi import HTTPException

from apme_gateway.api.schemas import AiProviderSchema

if TYPE_CHECKING:
    from apme_gateway.db.models import AiProvider

_PROVIDER_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def validate_provider_name(name: str) -> str:
    """Validate Abbenay virtual provider name.

    Args:
        name: Candidate provider id.

    Returns:
        Stripped valid name.

    Raises:
        HTTPException: 400 when the name is invalid.
    """
    trimmed = name.strip()
    if not trimmed or not _PROVIDER_NAME_RE.match(trimmed):
        raise HTTPException(
            status_code=400,
            detail="Provider name must match ^[a-z][a-z0-9-]*$",
        )
    return trimmed


def to_ai_provider_schema(row: AiProvider) -> AiProviderSchema:
    """Convert ORM row to API schema (API key masked).

    Args:
        row: AiProvider ORM instance.

    Returns:
        Pydantic response schema without secret values.
    """
    try:
        models = json.loads(row.models_json or "{}")
    except json.JSONDecodeError:
        models = {}
    if not isinstance(models, dict):
        models = {}
    try:
        extra = json.loads(row.extra_json or "{}")
    except json.JSONDecodeError:
        extra = {}
    if not isinstance(extra, dict):
        extra = {}
    return AiProviderSchema(
        id=row.id,
        name=row.name,
        engine=row.engine,
        base_url=row.base_url or "",
        models=models,
        has_api_key=bool(row.api_key),
        extra=extra,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def dump_models(models: dict[str, dict[str, object]]) -> str:
    """Serialize models map for DB storage.

    Args:
        models: Model id → params map.

    Returns:
        JSON string suitable for ``models_json``.
    """
    return json.dumps(models or {})


def dump_extra(extra: dict[str, object]) -> str:
    """Serialize extra metadata for DB storage.

    Args:
        extra: Engine-specific metadata object.

    Returns:
        JSON string suitable for ``extra_json``.
    """
    return json.dumps(extra or {})
