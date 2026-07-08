"""Tests for UBI volume permission verification script (ADR-061)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_SH = REPO_ROOT / "containers" / "podman" / "check-volume-permissions.sh"
BUILD_SH = REPO_ROOT / "containers" / "podman" / "build.sh"


def test_build_sh_invokes_volume_permission_check() -> None:
    """build.sh must run the ADR-061 volume permission gate after image build."""
    content = BUILD_SH.read_text(encoding="utf-8")
    assert "check-volume-permissions.sh" in content


def test_volume_permission_script_is_executable_bash() -> None:
    """Volume check script must exist and use bash with strict mode."""
    content = CHECK_SH.read_text(encoding="utf-8")
    assert content.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in content
    assert "apme-primary:latest" in content
    assert "/sessions" in content
    assert "apme-gateway:latest" in content
    assert "/data" in content
    assert "apme-galaxy-proxy:latest" in content
    assert "/cache" in content
    assert "APME_UBI_RUNTIME_UID:-1001" in content


@pytest.mark.skipif(  # type: ignore[untyped-decorator]
    os.environ.get("APME_SKIP_PODMAN") == "1",
    reason="Podman checks disabled via APME_SKIP_PODMAN=1",
)
def test_volume_permission_script_runs_when_images_exist() -> None:
    """Run the volume check when service images are already built."""
    required = ("apme-primary:latest", "apme-gateway:latest", "apme-galaxy-proxy:latest")
    missing = [
        image for image in required if subprocess.run(["podman", "image", "exists", image], check=False).returncode != 0
    ]
    if missing:
        pytest.skip(f"Images not built: {', '.join(missing)}")

    result = subprocess.run(
        ["bash", str(CHECK_SH)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
