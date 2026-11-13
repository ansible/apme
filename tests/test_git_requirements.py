"""Git requirement policy, credential and dependency isolation regressions."""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apme.v1.common_pb2 import File
from apme.v1.engine_pb2 import ScanChunk
from apme_engine.daemon import engine_upload
from apme_engine.daemon.engine_server import _discover_collection_specs
from apme_engine.daemon.git_dependencies import install_git_dependencies
from apme_engine.daemon.session import SessionState
from apme_gateway.scan import git_requirements as git


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "override,project_token,expected",
    [
        (None, "project", "project"),
        ("separate", "project", "separate"),
        ("", "project", "project"),
        (None, None, "deployment"),
    ],
)
async def test_credentials_and_dependency_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: str | None, project_token: str | None, expected: str
) -> None:
    """Git requirements use their override or default to project credentials.

    Args:
        tmp_path: Disposable project directory.
        monkeypatch: Isolated dependency overrides.
        override: Requirement credential.
        project_token: Project clone credential.
        expected: Credential used to fetch dependencies.
    """
    (tmp_path / "requirements.yml").write_text("""collections:
  - name: https://git.example/collection.git
    type: git
    version: v1
roles:
  - src: git+https://git.example/role.git
    name: example_role
    version: main
""")
    db = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                SimpleNamespace(enabled=True),
                SimpleNamespace(
                    requirements_token=override,
                    scm_token=project_token,
                    repo_url="https://git.example/project",
                    scm_provider="gitlab",
                ),
            ]
        )
    )

    @asynccontextmanager
    async def session():  # type: ignore[no-untyped-def]
        yield db

    monkeypatch.setattr(git, "get_session", session)
    monkeypatch.setenv("APME_SCM_TOKEN", "deployment")
    calls = []

    def fetch(url: str, ref: str, dest: str, token: str | None, scm_provider: str | None = None) -> None:
        assert scm_provider == "gitlab"
        calls.append((ref, token))
        root = Path(dest)
        if "collection" in url:
            (root / "galaxy.yml").write_text("namespace: demo\nname: custom\nversion: 1.0.0\n")
            (root / "plugins/modules").mkdir(parents=True)
            (root / "plugins/modules/probe.py").write_text("DOCUMENTATION = ''\n")
        else:
            (root / "tasks").mkdir()
            (root / "tasks/main.yml").write_text("- ansible.builtin.debug:\n    msg: hello\n")

    monkeypatch.setattr(git, "_fetch", fetch)
    result = await git.resolve_git_requirements("project", str(tmp_path))
    assert calls == [("v1", expected), ("main", expected)]
    assert {f.path for f in result} == {
        "ansible_collections/demo/custom/galaxy.yml",
        "ansible_collections/demo/custom/plugins/modules/probe.py",
        "roles/example_role/tasks/main.yml",
    }
    assert all(expected.encode() not in f.content for f in result)


async def test_disabled_policy_blocks_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The global switch is enforced before any Git dependency network access.

    Args:
        tmp_path: Disposable project directory.
        monkeypatch: Isolated dependency overrides.
    """
    (tmp_path / "requirements.yml").write_text("roles:\n- src: git+https://git.example/role.git\n")
    db = SimpleNamespace(get=AsyncMock(return_value=None))

    @asynccontextmanager
    async def session():  # type: ignore[no-untyped-def]
        yield db

    monkeypatch.setattr(git, "get_session", session)
    with pytest.raises(ValueError, match="disabled"):
        await git.resolve_git_requirements("project", str(tmp_path))


def test_git_collection_not_sent_to_galaxy() -> None:
    """Git URLs are never converted to Galaxy wheel package names."""
    specs, _ = _discover_collection_specs(
        [
            File(
                path="requirements.yml",
                content=b"collections:\n- name: https://git.example/repo.git\n  type: git\n- community.general\n",
            )
        ]
    )
    assert specs == ["community.general"]


def test_dependency_upload_is_not_project_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dependencies share admission limits but cannot become remediation targets.

    Args:
        monkeypatch: Isolated upload cap override.
    """
    session = SessionState(session_id="test")
    chunk = ScanChunk(
        files=[File(path="site.yml", content=b"---")],
        dependency_files=[File(path="roles/test/tasks/main.yml", content=b"[]")],
    )
    engine_upload._session_upload_append(session, chunk)
    assert session.working_files == {"site.yml": b"---"}
    assert session.dependency_files == {"roles/test/tasks/main.yml": b"[]"}
    before = engine_upload._hash_upload_chunk(chunk)
    engine_upload.dependency_upload_files(chunk)[0].content = b"changed"
    assert engine_upload._hash_upload_chunk(chunk) != before
    monkeypatch.setattr(engine_upload, "_SESSION_MAX_UPLOAD_FILES", 2)
    with pytest.raises(ValueError, match="file limit"):
        engine_upload._session_upload_append(
            session, ScanChunk(dependency_files=[File(path="roles/extra/tasks/main.yml", content=b"[]")])
        )
    assert len(session.dependency_files) == 1


def test_install_dependencies_and_reject_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only collection and role install paths can be materialized.

    Args:
        tmp_path: Disposable venv directory.
        monkeypatch: Site-packages override.
    """
    from apme_engine.daemon import git_dependencies

    site = tmp_path / "site-packages"
    site.mkdir()
    monkeypatch.setattr(git_dependencies, "_venv_site_packages", lambda _: site)
    with pytest.raises(ValueError):
        install_git_dependencies(tmp_path, {"../outside": b"bad"})
    install_git_dependencies(tmp_path, {"roles/test/tasks/main.yml": b"[]"})
    assert (site / "roles/test/tasks/main.yml").read_bytes() == b"[]"


def test_git_role_is_loaded_as_external_context(tmp_path: Path) -> None:
    """A fetched role is available to parsing without becoming project source.

    Args:
        tmp_path: Disposable dependency and project directories.
    """
    from apme_engine.engine.scan_state import SingleScan
    from apme_engine.runner import run_scan

    project = tmp_path / "project"
    project.mkdir()
    (project / "site.yml").write_text("- hosts: all\n  roles:\n    - external_role\n")
    dependencies = tmp_path / "dependencies"
    role = dependencies / "roles/external_role"
    (role / "tasks").mkdir(parents=True)
    (role / "tasks/main.yml").write_text("- name: From Git\n  ansible.builtin.debug:\n    msg: dependency\n")
    (role / "meta").mkdir()
    (role / "meta/main.yml").write_text("galaxy_info:\n  role_name: external_role\n")
    collection = dependencies / "ansible_collections/demo/custom"
    (collection / "roles/inner/tasks").mkdir(parents=True)
    (collection / "roles/inner/tasks/main.yml").write_text("- ansible.builtin.debug:\n    msg: collection\n")
    (collection / "galaxy.yml").write_text("namespace: demo\nname: custom\nversion: 1.0.0\n")
    context = run_scan(str(project), str(tmp_path / "loader"), dependency_dir=str(dependencies))
    assert isinstance(context.scandata, SingleScan)
    assert "role-external_role" in context.scandata.ext_definitions
    assert "collection-demo.custom" in context.scandata.ext_definitions
    assert list(project.iterdir()) == [project / "site.yml"]


def test_git_snapshot_changes_plugin_cache_version(tmp_path: Path) -> None:
    """Different Git payloads must not reuse plugin data with the same release version.

    Args:
        tmp_path: Disposable venv fixture directory.
    """
    from apme_engine.validators.ansible.cache import PluginCache

    versions = []
    for snapshot in ("first", "second"):
        venv = tmp_path / snapshot
        collection = venv / "lib/python3.12/site-packages/ansible_collections/demo/custom"
        collection.mkdir(parents=True)
        (collection / "MANIFEST.json").write_text('{"collection_info":{"version":"1.0.0"}}')
        (venv / ".git-dependencies-ready").write_text(snapshot)
        versions.append(PluginCache()._resolve_version(str(venv), "demo", "custom"))
    assert versions[0] != versions[1]
