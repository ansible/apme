"""Tests for Galaxy Proxy command-line logging configuration."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from galaxy_proxy.cli import _setup_logging, log_level_from_environment


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("configured", "expected"),
    [
        (None, logging.INFO),
        ("debug", logging.DEBUG),
        (" WARNING ", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("CRITICAL", logging.CRITICAL),
    ],
)
def test_log_level_from_environment(configured: str | None, expected: int) -> None:
    """Supported values are normalized and the default is INFO.

    Args:
        configured: Optional LOG_LEVEL environment value.
        expected: Expected logging level.
    """
    environment = {} if configured is None else {"LOG_LEVEL": configured}
    with patch.dict("os.environ", environment, clear=True):
        assert log_level_from_environment() == expected


def test_log_level_from_environment_rejects_invalid_value() -> None:
    """Unsupported levels fail startup configuration."""
    with (
        patch.dict("os.environ", {"LOG_LEVEL": "TRACE"}, clear=True),
        pytest.raises(ValueError, match="LOG_LEVEL must be one of"),
    ):
        log_level_from_environment()


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("configured", "verbose", "expected"),
    [
        ("CRITICAL", 1, logging.INFO),
        ("CRITICAL", 2, logging.DEBUG),
        ("DEBUG", 0, logging.DEBUG),
    ],
)
def test_verbose_flags_can_increase_configured_verbosity(
    configured: str,
    verbose: int,
    expected: int,
) -> None:
    """CLI verbosity flags can make the configured level more verbose.

    Args:
        configured: Configured LOG_LEVEL value.
        verbose: Number of verbose flags.
        expected: Expected resulting logging level.
    """
    with (
        patch.dict("os.environ", {"LOG_LEVEL": configured}, clear=True),
        patch("galaxy_proxy.cli.logging.basicConfig") as basic_config,
        patch("galaxy_proxy.cli.logging.getLogger") as get_logger,
    ):
        assert _setup_logging(verbose) == expected

    basic_config.assert_called_once_with(
        level=expected,
        format="%(levelname)s: %(message)s",
    )
    get_logger.return_value.setLevel.assert_called_once_with(expected)
