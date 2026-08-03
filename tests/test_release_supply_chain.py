"""Tests for release supply-chain scripts (issues #203, #204)."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SUPPLY_CHAIN_SH = REPO_ROOT / "containers" / "ci" / "supply-chain.sh"
PYTHON_SBOM_SH = REPO_ROOT / "containers" / "ci" / "generate-python-sbom.sh"
CONTAINER_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "container-images.yml"
IMAGES_FILE = REPO_ROOT / "containers" / "ci" / "images.txt"


def test_supply_chain_parallel_allowlist_includes_dispatch_function() -> None:
    """run_parallel must allow the function used by the task builder."""
    content = SUPPLY_CHAIN_SH.read_text(encoding="utf-8")
    assert "process_image_tag|process_ref)" in content.replace(" ", "")


def test_install_syft_verifies_checksum_before_install() -> None:
    """Syft install must pin a release and verify against committed checksums."""
    install_sh = REPO_ROOT / "containers" / "ci" / "install-syft.sh"
    checksums = REPO_ROOT / "containers" / "ci" / "syft-release-checksums.txt"
    content = install_sh.read_text(encoding="utf-8")
    checksum_content = checksums.read_text(encoding="utf-8")
    assert content.startswith("#!/usr/bin/env bash")
    assert "install_syft() (" in content
    assert "trap" in content and "EXIT" in content
    assert "syft-release-checksums.txt" in content
    assert "lookup_committed_checksum" in content
    assert "checksums_url" not in content
    assert "sha256_file" in content
    assert "sha256_string" in content
    assert "shasum -a 256" in content
    assert "Checksum mismatch" in content
    assert "github.com/anchore/syft/releases/download" in content
    assert "command -v syft" not in content
    assert "${HOME}/.cache/apme/bin" in content
    assert "SYFT_BIN" in content
    assert "1.21.0 linux amd64" in checksum_content
    assert "1.21.0 darwin arm64" in checksum_content
    assert "SYFT_CHECKSUMS_FILE:-" not in content


def test_install_syft_rejects_unsupported_release() -> None:
    """Unsupported SYFT_VERSION must fail before any download."""
    install_sh = REPO_ROOT / "containers" / "ci" / "install-syft.sh"
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"""
                set -euo pipefail
                export SYFT_INSTALL_DIR="{tmpdir}/bin"
                source {install_sh}
                SYFT_VERSION=0.0.0 install_syft
                """,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode != 0
    assert "Unsupported syft release/platform" in (result.stderr or result.stdout)


def test_install_syft_rejects_unsupported_architecture() -> None:
    """Unsupported host architecture must fail before any download."""
    install_sh = REPO_ROOT / "containers" / "ci" / "install-syft.sh"
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"""
                set -euo pipefail
                export SYFT_INSTALL_DIR="{tmpdir}/bin"
                uname() {{
                  case "$1" in
                    -s) echo linux ;;
                    -m) echo ppc64le ;;
                    *) command uname "$@" ;;
                  esac
                }}
                export -f uname
                source {install_sh}
                install_syft
                """,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode != 0
    assert "Unsupported architecture for syft install" in (result.stderr or result.stdout)


def test_python_sbom_builds_into_isolated_output_dir() -> None:
    """Wheel SBOM must come from this run's build, not stale dist/ artifacts."""
    content = PYTHON_SBOM_SH.read_text(encoding="utf-8")
    assert "mktemp -d" in content
    assert "--out-dir" in content
    assert "dist/*.whl" not in content


def test_container_workflow_runs_supply_chain_after_merge() -> None:
    """Published images must be signed and SBOM'd after manifest merge."""
    workflow = yaml.safe_load(CONTAINER_WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert "supply-chain" in jobs
    assert jobs["supply-chain"]["needs"] == "merge-manifests"
    assert jobs["merge-manifests"]["outputs"]["consumer_tags"] == "${{ steps.tags.outputs.tags }}"

    supply_steps = jobs["supply-chain"]["steps"]
    step_names = [step.get("name", "") for step in supply_steps if isinstance(step, dict)]
    assert "Restore consumer tags from merge-manifests" in step_names
    assert "docker/metadata-action@" not in yaml.dump(jobs["supply-chain"])

    run_blocks = " ".join(step["run"] for step in supply_steps if isinstance(step, dict) and "run" in step)
    assert "supply-chain.sh" in run_blocks
    assert "generate-python-sbom.sh" in run_blocks

    uses_blocks = " ".join(step["uses"] for step in supply_steps if isinstance(step, dict) and "uses" in step)
    assert "cosign-installer" in uses_blocks
    assert "attest-build-provenance" not in uses_blocks

    attest_steps = jobs["attest-provenance"]["steps"]
    attest_uses = [step["uses"] for step in attest_steps if isinstance(step, dict) and "uses" in step]
    assert any(value.startswith("actions/attest-build-provenance@") for value in attest_uses), attest_uses

    checkout_steps = [
        step
        for step in supply_steps + jobs["attest-provenance"]["steps"]
        if isinstance(step, dict) and step.get("uses", "").startswith("actions/checkout@")
    ]
    assert all(step.get("with", {}).get("persist-credentials") is False for step in checkout_steps)


def test_signing_permissions_are_job_scoped() -> None:
    """Signing permissions must be scoped to supply-chain and attest-provenance only."""
    workflow = yaml.safe_load(CONTAINER_WORKFLOW.read_text(encoding="utf-8"))
    workflow_perms = workflow.get("permissions", {})
    assert "id-token" not in workflow_perms
    assert "attestations" not in workflow_perms

    jobs = workflow["jobs"]
    assert jobs["supply-chain"]["permissions"]["id-token"] == "write"
    assert "attestations" not in jobs["supply-chain"].get("permissions", {})
    assert jobs["attest-provenance"]["permissions"]["attestations"] == "write"
    for name in ("build-base", "build-images", "merge-manifests"):
        assert "id-token" not in jobs[name].get("permissions", {})


def test_container_workflow_rejects_empty_provenance_subjects() -> None:
    """Empty [] subjects must fail before attest-provenance matrix expansion."""
    content = CONTAINER_WORKFLOW.read_text(encoding="utf-8")
    assert "jq 'length' /tmp/subjects.json" in content
    assert "No provenance subjects produced" in content


def test_attest_provenance_logs_into_quay_when_configured() -> None:
    """Quay matrix legs need registry credentials for push-to-registry."""
    workflow = yaml.safe_load(CONTAINER_WORKFLOW.read_text(encoding="utf-8"))
    attest_job = workflow["jobs"]["attest-provenance"]
    assert attest_job["needs"] == ["merge-manifests", "supply-chain"]

    attest_steps = attest_job["steps"]
    step_ids = [step.get("id", "") for step in attest_steps if isinstance(step, dict)]
    assert "quay-check" not in step_ids
    assert "owner" not in step_ids

    login_steps = [
        step
        for step in attest_steps
        if isinstance(step, dict) and step.get("uses", "").startswith("docker/login-action@")
    ]
    quay_login = next(
        step for step in login_steps if step.get("if", "").endswith("merge-manifests.outputs.quay_enabled == 'true'")
    )
    assert quay_login["with"]["registry"] == "${{ env.QUAY_REGISTRY }}"
    assert quay_login["with"]["username"] == "${{ secrets.QUAY_USERNAME }}"


def test_supply_chain_validates_cosign_before_registry_work() -> None:
    """Cosign must be checked once before parallel image processing starts."""
    content = SUPPLY_CHAIN_SH.read_text(encoding="utf-8")
    process_ref_body = content.split("process_ref() {", 1)[1].split("\n}\n", 1)[0]
    assert "command -v cosign" not in process_ref_body
    assert content.index("command -v cosign") < content.index('run_parallel "${tasks[@]}"')


def test_supply_chain_script_is_executable_bash() -> None:
    """Supply-chain script must exist and use bash with strict mode."""
    content = SUPPLY_CHAIN_SH.read_text(encoding="utf-8")
    assert content.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in content
    assert "cosign sign" in content
    assert "cyclonedx-json@1.5" in content
    assert "cosign attest" in content
    assert "images.txt" in content
    assert '"${SYFT_BIN}" "${ref}@${digest}"' in content
    assert "${LIST_SUBJECTS}.d/" in content
    assert "write-provenance-subjects.sh" in content


def test_python_sbom_script_is_executable_bash() -> None:
    """Python SBOM script must build a wheel and emit CycloneDX JSON."""
    content = PYTHON_SBOM_SH.read_text(encoding="utf-8")
    assert content.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in content
    assert "uv build --wheel" in content
    assert "cyclonedx-json@1.5" in content
    assert '"${SYFT_BIN}"' in content


def test_supply_chain_help_exits_zero() -> None:
    """--help should be a safe local entrypoint."""
    result = subprocess.run(
        ["bash", str(SUPPLY_CHAIN_SH), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "--tags-file" in result.stdout


def test_python_sbom_help_exits_zero() -> None:
    """--help should be a safe local entrypoint."""
    result = subprocess.run(
        ["bash", str(PYTHON_SBOM_SH), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "--output-dir" in result.stdout


def test_subjects_json_format_from_supply_chain_skip_sign() -> None:
    """Provenance subjects must flow through supply-chain.sh dispatch and deduplication."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        bindir = root / "bin"
        bindir.mkdir()
        output_dir = root / "sboms"
        subjects_file = root / "subjects.json"
        images_file = root / "images.txt"
        tags_file = root / "tags.txt"
        images_file.write_text("primary\ngateway\n", encoding="utf-8")
        tags_file.write_text("v1.0.0\n", encoding="utf-8")

        docker_stub = """\
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "buildx" && "${2:-}" == "imagetools" && "${3:-}" == "inspect" ]]; then
  case "${4:-}" in
    *apme-primary*) echo "sha256:aaa111" ;;
    *apme-gateway*) echo "sha256:bbb222" ;;
    *)
      echo "unexpected image ref: ${4:-}" >&2
      exit 1
      ;;
  esac
  exit 0
fi
echo "unexpected docker invocation: $*" >&2
exit 1
"""
        syft_stub = """\
#!/usr/bin/env bash
set -euo pipefail
echo '{"bomFormat":"CycloneDX","specVersion":"1.5","version":1,"components":[]}'
"""
        for name, body in (("docker", docker_stub), ("syft", syft_stub)):
            script = bindir / name
            script.write_text(body, encoding="utf-8")
            script.chmod(0o755)

        result = subprocess.run(
            [
                "bash",
                "-c",
                f"""
                set -euo pipefail
                export PATH="{bindir}:$PATH"
                export SYFT_INSTALL_DIR="{bindir}"
                export IMAGES_FILE="{images_file}"
                export SUPPLY_CHAIN_PARALLELISM=2
                {SUPPLY_CHAIN_SH} \\
                  --owner ansible \\
                  --tags-file {tags_file} \\
                  --output-dir {output_dir} \\
                  --skip-sign \\
                  --list-subjects {subjects_file}
                """,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        subjects = json.loads(subjects_file.read_text(encoding="utf-8"))
    assert sorted(subjects, key=lambda item: item["name"]) == [
        {"name": "ghcr.io/ansible/apme-gateway", "digest": "sha256:bbb222"},
        {"name": "ghcr.io/ansible/apme-primary", "digest": "sha256:aaa111"},
    ]


def test_subjects_json_format_from_supply_chain_helper() -> None:
    """Provenance subject list must be valid JSON for the attest matrix."""
    helper = REPO_ROOT / "containers" / "ci" / "write-provenance-subjects.sh"
    with tempfile.TemporaryDirectory() as tmpdir:
        subjects_file = Path(tmpdir) / "subjects.json"
        tmp_file = Path(f"{subjects_file}.tmp")
        tmp_file.write_text(
            "\n".join(
                [
                    "ghcr.io/ansible/apme-primary\tsha256:abc",
                    "ghcr.io/ansible/apme-primary\tsha256:abc",
                    "ghcr.io/ansible/apme-gateway\tsha256:def",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                "bash",
                "-c",
                f"""
                set -euo pipefail
                source {helper}
                write_provenance_subjects {subjects_file}
                cat {subjects_file}
                """,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    subjects = json.loads(result.stdout)
    assert sorted(subjects, key=lambda item: item["name"]) == [
        {"name": "ghcr.io/ansible/apme-gateway", "digest": "sha256:def"},
        {"name": "ghcr.io/ansible/apme-primary", "digest": "sha256:abc"},
    ]


def test_subjects_json_rejects_malformed_rows() -> None:
    """Malformed provenance subject rows must fail instead of being dropped silently."""
    helper = REPO_ROOT / "containers" / "ci" / "write-provenance-subjects.sh"
    with tempfile.TemporaryDirectory() as tmpdir:
        subjects_file = Path(tmpdir) / "subjects.json"
        tmp_file = Path(f"{subjects_file}.tmp")
        tmp_file.write_text(
            "ghcr.io/ansible/apme-primary\tsha256:abc\n"
            "missing-digest-column\n"
            "ghcr.io/ansible/apme-gateway\tsha256:def\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                "bash",
                "-c",
                f"""
                set -euo pipefail
                source {helper}
                write_provenance_subjects {subjects_file}
                """,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode != 0
    assert "malformed provenance subject row" in (result.stderr or result.stdout)


def test_subjects_json_rejects_extra_fields() -> None:
    """Rows with more than two tab-separated fields must fail explicitly."""
    helper = REPO_ROOT / "containers" / "ci" / "write-provenance-subjects.sh"
    with tempfile.TemporaryDirectory() as tmpdir:
        subjects_file = Path(tmpdir) / "subjects.json"
        tmp_file = Path(f"{subjects_file}.tmp")
        tmp_file.write_text(
            "ghcr.io/ansible/apme-primary\tsha256:abc\nghcr.io/ansible/apme-gateway\tsha256:def\textra-field\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                "bash",
                "-c",
                f"""
                set -euo pipefail
                source {helper}
                write_provenance_subjects {subjects_file}
                """,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode != 0
    assert "malformed provenance subject row" in (result.stderr or result.stdout)


def test_subjects_json_normalizes_crlf_input() -> None:
    """CRLF subject rows must normalize to the same digest as LF rows."""
    helper = REPO_ROOT / "containers" / "ci" / "write-provenance-subjects.sh"
    with tempfile.TemporaryDirectory() as tmpdir:
        subjects_file = Path(tmpdir) / "subjects.json"
        tmp_file = Path(f"{subjects_file}.tmp")
        tmp_file.write_bytes(
            b"ghcr.io/ansible/apme-primary\tsha256:abc\r\nghcr.io/ansible/apme-gateway\tsha256:def\r\n"
        )

        result = subprocess.run(
            [
                "bash",
                "-c",
                f"""
                set -euo pipefail
                source {helper}
                write_provenance_subjects {subjects_file}
                cat {subjects_file}
                """,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    subjects = json.loads(result.stdout)
    assert sorted(subjects, key=lambda item: item["name"]) == [
        {"name": "ghcr.io/ansible/apme-gateway", "digest": "sha256:def"},
        {"name": "ghcr.io/ansible/apme-primary", "digest": "sha256:abc"},
    ]


def test_images_file_lists_published_services() -> None:
    """Supply-chain reads the same image inventory as merge-manifests."""
    names = {
        line.strip()
        for line in IMAGES_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for image_name in ("primary", "gateway", "ui"):
        assert image_name in names
