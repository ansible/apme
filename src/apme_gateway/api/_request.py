"""Shared FastAPI request alias (#30).

Single definition of the Optional/required ``Request`` alias used by
Gateway routers. Type-checkers see the Optional form (direct callers may
pass None); at runtime FastAPI injection requires the bare ``Request``
annotation (``Request | None`` breaks OpenAPI model generation — a Union
is treated as a Body/Query field).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias

from fastapi import Request

if TYPE_CHECKING:
    _RequestOpt: TypeAlias = Request | None  # noqa: UP040 -- `type` keyword is lazy; FastAPI needs a real alias
else:
    _RequestOpt: TypeAlias = Request  # noqa: UP040 -- runtime keeps the bare form for injection
