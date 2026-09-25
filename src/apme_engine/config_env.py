"""Shared environment-variable parsing with safe fallbacks.

Single canonical home for numeric env parsing so timeout/cap guards cannot
drift (zero/negative/NaN/inf must degrade to documented defaults, never crash
the daemon, invert a guard, or turn "wait forever" into "fail immediately").
"""

from __future__ import annotations

import logging
import math
import os

logger = logging.getLogger(__name__)


def get_env_float(name: str, default: float, *, positive_only: bool = False) -> float:
    """Parse *name* as a finite float, falling back to *default* with a warning.

    Args:
        name: Environment variable name.
        default: Value used when unset, unparsable, or non-finite.
        positive_only: When True, non-positive values also fall back.

    Returns:
        Finite (and positive when requested) float value.
    """
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r — using default %s", name, raw, default)
        return default
    if not math.isfinite(value):
        logger.warning("Non-finite %s=%r — using default %s", name, raw, default)
        return default
    if positive_only and value <= 0.0:
        logger.warning("Non-positive %s=%r — using default %s", name, raw, default)
        return default
    return value


def get_env_int(
    name: str,
    default: int,
    *,
    min_value: int | None = None,
) -> int:
    """Parse *name* as an int, falling back to *default* with a warning.

    Args:
        name: Environment variable name.
        default: Value used when unset or invalid.
        min_value: When set, values below it fall back to *default*.

    Returns:
        Parsed integer honoring the floor.
    """
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using default %d", name, raw, default)
        return default
    if min_value is not None and value < min_value:
        logger.warning("Invalid %s=%r; using default %d", name, raw, default)
        return default
    return value
