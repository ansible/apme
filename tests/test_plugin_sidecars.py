"""Tests for example Plugin sidecars (OPA custom + ansible-security-scanner)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _load_plugin(subdir: str) -> ModuleType:
    """Import an example plugin module from ``examples/plugins/<subdir>/plugin.py``.

    Args:
        subdir: Directory name under ``examples/plugins``.

    Returns:
        Loaded module.
    """
    path = Path(__file__).resolve().parents[1] / "examples" / "plugins" / subdir / "plugin.py"
    spec = importlib.util.spec_from_file_location(f"example_plugin_{subdir}", path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_opa_input_document_wraps_list() -> None:
    """Lists become ``{"hierarchy": ...}`` for ``opa eval -I``."""
    mod = _load_plugin("opa-custom")
    assert mod.opa_input_document([{"nodes": []}]) == {"hierarchy": [{"nodes": []}]}
    assert mod.opa_input_document(None) == {"hierarchy": []}
    already = {"hierarchy": [{"nodes": [{"module": "x"}]}]}
    assert mod.opa_input_document(already) is already


def test_parse_opa_eval_stdout_extracts_value_array() -> None:
    """OPA eval JSON shape matches ``opa_client.run_opa``."""
    mod = _load_plugin("opa-custom")
    payload = {
        "result": [
            {
                "expressions": [
                    {
                        "value": [
                            {
                                "rule_id": "EXT-opacustom-001",
                                "message": "Banned collection module: community.general.apk",
                                "file": "site.yml",
                                "line": 4,
                                "path": "site.yml/plays[0]/tasks[0]",
                                "severity": "high",
                                "scope": "task",
                                "ai_guidance": "Replace community.general modules.",
                            }
                        ]
                    }
                ]
            }
        ]
    }
    rows = mod.parse_opa_eval_stdout(json.dumps(payload))
    assert len(rows) == 1
    plugin = mod.OpaCustomPlugin()
    item = mod.map_opa_row(plugin, rows[0])
    assert item["rule_id"] == "EXT-opacustom-001"
    assert item["line"] == 4
    assert "community.general.apk" in str(item["message"])
    assert item["ai_guidance"] == "Replace community.general modules."


def test_opa_validate_uses_eval_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    """``validate`` maps eval_bundle rows without calling the opa binary.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    mod = _load_plugin("opa-custom")

    def _fake_eval(bundle: Path, entrypoint: str, hierarchy: object) -> list[dict[str, object]]:
        del bundle, entrypoint, hierarchy
        return [{"rule_id": "001", "message": "banned", "file": "a.yml", "line": 2}]

    monkeypatch.setattr(mod, "eval_bundle", _fake_eval)
    findings = mod.OpaCustomPlugin().validate([], {"hierarchy": []})
    assert findings[0]["rule_id"] == "EXT-opacustom-001"
    assert findings[0]["file"] == "a.yml"


def test_opa_custom_health_without_opa(monkeypatch: pytest.MonkeyPatch) -> None:
    """Health fails closed when the ``opa`` binary is missing.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    mod = _load_plugin("opa-custom")
    monkeypatch.setattr("shutil.which", lambda *_args: None)
    assert "opa binary" in mod.OpaCustomPlugin().health()


def test_secscan_safe_relpath_rejects_escape() -> None:
    """Absolute and parent paths are not written to the temp tree."""
    mod = _load_plugin("secscan")
    assert mod.safe_relpath("playbooks/site.yml") == Path("playbooks/site.yml")
    assert mod.safe_relpath("/etc/passwd") is None
    assert mod.safe_relpath("../secret.yml") is None


def test_secscan_scan_relpath_strips_tmp_root(tmp_path: Path) -> None:
    """Absolute scanner paths under the temp tree become Engine-relative.

    Args:
        tmp_path: Pytest temporary directory.
    """
    mod = _load_plugin("secscan")
    abs_hit = tmp_path / "playbooks" / "site.yml"
    abs_hit.parent.mkdir(parents=True)
    abs_hit.write_text("---\n", encoding="utf-8")
    assert mod.scan_relpath(tmp_path, str(abs_hit)) == "playbooks/site.yml"
    assert mod.scan_relpath(tmp_path, "/etc/passwd") == ""


def test_secscan_map_finding_prefixes_rule_id() -> None:
    """Scanner rule IDs become ``EXT-secscan-<id>`` with recommendation as AI hint."""
    mod = _load_plugin("secscan")
    plugin = mod.SecscanPlugin()
    finding = SimpleNamespace(
        file_path="roles/app/tasks/main.yml",
        line_number=12,
        rule_id="hardcoded_password",
        severity="HIGH",
        title="Hardcoded password",
        description="A literal password was found.",
        recommendation="Move the secret to Ansible Vault.",
    )
    item = mod.map_finding(plugin, finding)
    assert item["rule_id"] == "EXT-secscan-hardcoded_password"
    assert item["line"] == 12
    assert item["file"] == "roles/app/tasks/main.yml"
    assert "Hardcoded password" in str(item["message"])
    assert item["ai_guidance"] == "Move the secret to Ansible Vault."
    assert plugin.transform_rule_ids() == []


def test_secscan_validate_without_package() -> None:
    """When the scanner extra is not installed, Validate raises.

    Engine then records ``EXT-secscan-unavailable`` instead of a silent pass.
    """
    mod = _load_plugin("secscan")
    if getattr(mod, "_Scanner", None) is not None:
        pytest.skip("ansible-security-scanner is installed")
    with pytest.raises(RuntimeError, match="ansible-security-scanner"):
        mod.SecscanPlugin().validate([("site.yml", b"---\n")], None)
    assert "not installed" in mod.SecscanPlugin().health()


def test_secscan_write_file_tree(tmp_path: Path) -> None:
    """Only safe relative paths are materialized.

    Args:
        tmp_path: Pytest temporary directory.
    """
    mod = _load_plugin("secscan")
    written = mod.write_file_tree(
        tmp_path,
        [
            ("site.yml", b"---\n"),
            ("../escape.yml", b"nope"),
            ("roles/x/tasks/main.yml", b"ok"),
        ],
    )
    assert written == ["site.yml", "roles/x/tasks/main.yml"]
    assert (tmp_path / "site.yml").read_bytes() == b"---\n"
    assert (tmp_path / "roles/x/tasks/main.yml").read_bytes() == b"ok"
    assert not (tmp_path / "escape.yml").exists()


def test_plugin_sidecars_do_not_publish_host_port() -> None:
    """Plugin gRPC stays pod-local; 50100/50101 must not be hostPort."""
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "examples/plugins/pod-sidecars.yaml",
        "containers/podman/pod.yaml",
        "docs/guides/PLUGIN_SIDECARS.md",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        assert "hostPort: 50100" not in text
        assert "hostPort: 50101" not in text
