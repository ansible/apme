"""Git requirement policy, credential and dependency isolation regressions."""

import asyncio
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol
from unittest.mock import AsyncMock

import pytest

from apme.v1.common_pb2 import File
from apme.v1.engine_pb2 import ScanChunk
from apme_engine.daemon import engine_upload
from apme_engine.daemon.engine_server import _discover_collection_specs
from apme_engine.daemon.git_dependencies import install_git_dependencies
from apme_engine.daemon.session import SessionState
from apme_gateway.scan import git_requirements as git


class _Snapshot(Protocol):
    def __call__(
        self,
        url: str,
        ref: str,
        token: str | None = None,
        *,
        scm_provider: str | None = None,
        url_userpass: tuple[str, str] | None = None,
    ) -> AbstractAsyncContextManager[Path]: ...


def _snapshot_mock(
    fetcher: Callable[[str, str, str, str | None, str | None, tuple[str, str] | None], Awaitable[None]],
) -> _Snapshot:
    """Adapt a fake fetch to the bounded snapshot context contract.

    Args:
        fetcher: Fixture writer replacing network fetches.

    Returns:
        Snapshot context factory.
    """

    @asynccontextmanager
    async def snapshot(
        url: str,
        ref: str,
        token: str | None = None,
        *,
        scm_provider: str | None = None,
        url_userpass: tuple[str, str] | None = None,
    ) -> AsyncIterator[Path]:
        with tempfile.TemporaryDirectory() as directory:
            await fetcher(url, ref, directory, token, scm_provider, url_userpass)
            yield Path(directory)

    return snapshot


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "override,project_token,expected,operation_token,source_origin",
    [
        (None, "project", "project", None, "git.example"),
        ("separate", "project", "separate", None, "git.example"),
        ("", "project", "project", None, "git.example"),
        (None, None, "deployment", None, "git.example"),
        (None, "project", "operation", "operation", "git.example"),
        ("separate", "project", "separate", "operation", "git.example"),
        ("separate", "project", None, None, "other.example"),
    ],
)
async def test_credentials_and_dependency_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: str | None,
    project_token: str | None,
    expected: str | None,
    operation_token: str | None,
    source_origin: str,
) -> None:
    """Git requirements use their override or default to project credentials.

    Args:
        tmp_path: Disposable project directory.
        monkeypatch: Isolated dependency overrides.
        override: Requirement credential.
        project_token: Project clone credential.
        expected: Credential used to fetch dependencies.
        operation_token: Effective project clone credential.
        source_origin: Git dependency hostname.
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
    path = tmp_path / "requirements.yml"
    path.write_text(path.read_text().replace("git.example", source_origin))
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

    async def fetch(
        url: str,
        ref: str,
        dest: str,
        token: str | None,
        scm_provider: str | None = None,
        url_userpass: tuple[str, str] | None = None,
    ) -> None:
        assert url_userpass is None
        assert scm_provider == ("gitlab" if source_origin == "git.example" else None)
        calls.append((ref, token))
        root = Path(dest)
        if "collection" in url:
            (root / "galaxy.yml").write_text("namespace: demo\nname: custom\nversion: 1.0.0\n")
            (root / "plugins/modules").mkdir(parents=True)
            (root / "plugins/modules/probe.py").write_text("DOCUMENTATION = ''\n")
        else:
            (root / "tasks").mkdir()
            (root / "tasks/main.yml").write_text("- ansible.builtin.debug:\n    msg: hello\n")

    monkeypatch.setattr(git, "git_snapshot", _snapshot_mock(fetch))
    result = await git.resolve_git_requirements(
        "project", str(tmp_path), repo_url="https://git.example/project", scm_token=operation_token
    )
    assert calls == [("v1", expected), ("main", expected)]
    assert {f.path for f in result} == {
        "ansible_collections/demo/custom/galaxy.yml",
        "ansible_collections/demo/custom/plugins/modules/probe.py",
        "roles/example_role/tasks/main.yml",
    }
    if expected is not None:
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
        await git.resolve_git_requirements("project", str(tmp_path), repo_url="https://git.example/project")


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
    (dependencies / ".apme-git-dependencies.json").write_text(
        '{"roles":["external_role"],"collections":["demo.custom"]}'
    )
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


def test_dependency_hash_has_unambiguous_framing() -> None:
    """Null bytes cannot turn one file into an equivalent two-file snapshot."""
    assert engine_upload._hash_upload_files({"roles/r/a": b"x\0roles/r/b\0y"}) != engine_upload._hash_upload_files(
        {"roles/r/a": b"x", "roles/r/b": b"y"}
    )


def test_archive_sources_are_not_git() -> None:
    """HTTPS archives retain their existing non-Git handling."""
    from apme_engine.git_requirements import is_git_requirement

    assert not is_git_requirement({"src": "https://example.test/role.tar.gz"})
    assert not is_git_requirement({"name": "https://example.test/collection.tar.gz", "type": "url"})
    assert is_git_requirement({"src": "https://example.test/role", "scm": "git"})


def test_metadata_limits_precede_parsing(tmp_path: Path) -> None:
    """Oversized metadata is rejected before loading its YAML.

    Args:
        tmp_path: Disposable Git checkout.
    """
    (tmp_path / "galaxy.yml").write_bytes(b" " * (2 * 1024 * 1024 + 1))
    with pytest.raises(RuntimeError, match="metadata exceeds"):
        git._collect_files("collections", {}, tmp_path, tmp_path, "collection.git")


async def test_fetch_cancellation_waits_for_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancellation terminates the Git process group before returning.

    Args:
        monkeypatch: Process lifecycle overrides.
    """
    created, stopped = asyncio.Event(), asyncio.Event()
    process = SimpleNamespace(pid=12345, returncode=None, stdout=None, stderr=None)

    async def wait() -> int:
        await stopped.wait()
        process.returncode = -15
        return -15

    process.wait = wait

    async def create(*args: object, **kwargs: object) -> object:
        created.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(os, "killpg", lambda *_: stopped.set())
    from apme_gateway.scm.safe_fetch import _run

    task = asyncio.create_task(_run(["git"], {}))
    await created.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert process.returncode == -15


def test_git_roles_preserve_project_role_search_paths(tmp_path: Path) -> None:
    """Appending Git roles preserves ansible.cfg paths for existing local roles.

    Args:
        tmp_path: Disposable project and venv fixtures.
    """
    from apme_engine.validators.ansible.rules import L057_syntax

    project = tmp_path / "project"
    local = project / "custom_roles/local/tasks"
    local.mkdir(parents=True)
    (local / "main.yml").write_text("[]")
    (project / "ansible.cfg").write_text("[defaults]\nroles_path = custom_roles\nlibrary = .\n")
    (project / "json.py").write_text(
        "from pathlib import Path\nPath('executed').touch()\nraise AssertionError('project import')\n"
    )
    (project / "site.yml").write_text("- hosts: localhost\n  gather_facts: false\n  roles: [local, fetched]\n")
    venv = tmp_path / "venv"
    roles = venv / "lib/python3.12/site-packages/roles/fetched/tasks"
    roles.mkdir(parents=True)
    (roles / "main.yml").write_text("[]")
    (venv / "bin").mkdir()
    (venv / "bin/python").write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    (venv / "bin/python").chmod(0o755)
    (venv / "bin/ansible-playbook").symlink_to(Path(sys.prefix) / "bin/ansible-playbook")
    assert L057_syntax.run(venv, project) == []
    assert not (project / "executed").exists()


def test_git_dependency_identifiers_cannot_escape_index_paths() -> None:
    """Collection identifiers cannot become absolute paths in the source index."""
    for path in ("ansible_collections/.../x/plugins/a.py", "roles/..hidden/tasks/main.yml"):
        with pytest.raises(ValueError, match="Invalid dependency"):
            engine_upload.validate_dependency_path(path)


def test_invalid_requirement_file_does_not_block_other_files(tmp_path: Path) -> None:
    """Malformed requirements do not suppress valid declarations in other files.

    Args:
        tmp_path: Project checkout.
    """
    (tmp_path / "requirements.yml").write_text("collections: [")
    (tmp_path / "roles").mkdir()
    (tmp_path / "roles/requirements.yml").write_text("- src: git+https://example.com/example.git\n")
    assert list(git._read_declarations(tmp_path)) == [("roles", {"src": "git+https://example.com/example.git"})]


def test_missing_collection_metadata_reports_error(tmp_path: Path) -> None:
    """Missing collection metadata produces a useful error.

    Args:
        tmp_path: Repository checkout.
    """
    with pytest.raises(ValueError, match="metadata.*not found"):
        git._collect_files("collections", {}, tmp_path, tmp_path, "test")


@pytest.mark.parametrize("output", ["not json", "null", '["ok", 42]'])  # type: ignore[untyped-decorator]
def test_role_path_probe_errors_are_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    """Bad role path probes produce an L057 result instead of aborting all validation.

    Args:
        tmp_path: Temporary venv and project.
        monkeypatch: Subprocess override.
        output: Invalid probe output.
    """
    from apme_engine.validators.ansible.rules import L057_syntax

    venv = tmp_path / "venv"
    (venv / "lib/python3.12/site-packages/roles").mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "bin/ansible-playbook").touch()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output))
    violations = L057_syntax.run(venv, tmp_path)
    assert len(violations) == 1
    assert violations[0]["rule_id"] == "L057"
    assert violations[0]["message"] == "Unable to resolve Ansible role search paths"


async def test_project_url_credentials_follow_same_origin_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """URL credentials are inherited only for same-origin dependencies.

    Args:
        tmp_path: Project checkout.
        monkeypatch: Isolated gateway state.
    """
    (tmp_path / "requirements.yml").write_text(
        "roles:\n- src: git+https://git.example/one.git\n- src: git+https://other.example/two.git\n"
    )
    db = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                SimpleNamespace(enabled=True),
                SimpleNamespace(
                    requirements_token=None,
                    scm_token=None,
                    repo_url="https://user:secret@git.example/project",
                    scm_provider=None,
                ),
            ]
        )
    )

    @asynccontextmanager
    async def session():  # type: ignore[no-untyped-def]
        yield db

    monkeypatch.setattr(git, "get_session", session)
    monkeypatch.setenv("APME_SCM_TOKEN", "")
    seen = []

    async def fetch(
        url: str,
        ref: str,
        dest: str,
        token: str | None,
        scm_provider: str | None = None,
        url_userpass: tuple[str, str] | None = None,
    ) -> None:
        seen.append((url, token, url_userpass))
        tasks = Path(dest) / "tasks"
        tasks.mkdir()
        (tasks / "main.yml").write_text("[]")

    monkeypatch.setattr(git, "git_snapshot", _snapshot_mock(fetch))
    await git.resolve_git_requirements("p", str(tmp_path), repo_url="https://user:secret@git.example/project")
    assert seen == [
        ("https://git.example/one.git", None, ("user", "secret")),
        ("https://other.example/two.git", None, None),
    ]


async def test_git_snapshot_isolated_from_later_galaxy_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Newly discovered Galaxy specs select another venv before the Git snapshot is applied.

    Args:
        tmp_path: Temporary fake venvs.
        monkeypatch: Scan and venv mocks.
    """
    from apme_engine.daemon import engine_server, git_dependencies
    from apme_engine.venv_manager import session as venv_module

    calls = []
    initial_root = tmp_path / "initial"
    final_root = tmp_path / "final"
    initial_root.mkdir()
    final_root.mkdir()
    initial = SimpleNamespace(venv_root=initial_root, failed_collections=[], installed_collections=[])
    final = SimpleNamespace(
        venv_root=final_root, failed_collections=["unavailable.collection"], installed_collections=[]
    )

    def peek(sid: str, version: str, specs: list[str]) -> object:
        calls.append((sid, tuple(specs)))
        return final if specs else initial

    manager = SimpleNamespace(peek_warm=peek, get=lambda *_: initial)
    monkeypatch.setattr(engine_server.EngineServicer, "_get_venv_manager", lambda _: manager)
    monkeypatch.setattr(venv_module, "_venv_site_packages", lambda _: tmp_path)
    monkeypatch.setattr(
        engine_server,
        "run_scan",
        lambda *args, **kwargs: SimpleNamespace(
            hierarchy_payload={"collection_set": ["example.new"]},
            scandata=None,
        ),
    )
    monkeypatch.setattr(git_dependencies, "_venv_site_packages", lambda root: root)
    dependency = {"ansible_collections/demo/custom/plugins/modules/a.py": b"git-version"}
    with pytest.raises(engine_server.CollectionDependencyError):
        await engine_server.EngineServicer()._execute_scan_pipeline(
            tmp_path,
            [],
            "scan",
            session_id="project",
            dependency_files=dependency,
        )
    assert calls[0][0] != calls[1][0]
    assert calls[0][1] == ()
    assert calls[1][1] == ("example.new",)
    assert not (final_root / ".git-dependencies-ready").exists()

    # A retry may install a conflicting transitive Galaxy collection before
    # the snapshot is written. Git must win and the marker must be created only
    # after the acquisition succeeds.
    galaxy_file = final_root / "ansible_collections/demo/custom/plugins/modules/a.py"
    galaxy_file.parent.mkdir(parents=True)
    galaxy_file.write_bytes(b"galaxy-version")
    final.failed_collections = []

    def stop_after_install(*args: object, **kwargs: object) -> object:
        raise RuntimeError("validators not needed")

    monkeypatch.setattr(engine_server, "ValidateRequest", stop_after_install)
    with pytest.raises(RuntimeError, match="validators not needed"):
        await engine_server.EngineServicer()._execute_scan_pipeline(
            tmp_path,
            [],
            "scan",
            session_id="project",
            dependency_files=dependency,
        )
    assert galaxy_file.read_bytes() == b"git-version"
    assert (final_root / ".git-dependencies-ready").is_file()


async def test_clone_origin_controls_credentials_after_project_url_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later project URL change cannot redirect the clone credential.

    Args:
        tmp_path: Disposable project directory.
        monkeypatch: Isolated dependency overrides.
    """
    (tmp_path / "requirements.yml").write_text("roles:\n- src: git+https://new.example/role.git\n")
    db = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                SimpleNamespace(enabled=True),
                SimpleNamespace(
                    requirements_token=None, scm_token=None, repo_url="https://new.example/project", scm_provider=None
                ),
            ]
        )
    )

    @asynccontextmanager
    async def session():  # type: ignore[no-untyped-def]
        yield db

    seen = []

    async def fetch(
        url: str,
        ref: str,
        dest: str,
        token: str | None,
        scm_provider: str | None = None,
        url_userpass: tuple[str, str] | None = None,
    ) -> None:
        seen.append((token, url_userpass))
        (Path(dest) / "main.yml").write_text("[]")

    monkeypatch.setattr(git, "get_session", session)
    monkeypatch.setattr(git, "git_snapshot", _snapshot_mock(fetch))
    await git.resolve_git_requirements("p", str(tmp_path), repo_url="https://old.example/project", scm_token="secret")
    assert seen == [(None, None)]


def test_collection_metadata_rejects_alias_graph(tmp_path: Path) -> None:
    """Reject structured metadata before converting an aliased graph to text.

    Args:
        tmp_path: Disposable Git checkout.
    """
    (tmp_path / "galaxy.yml").write_text("node: &node [a, b]\nnamespace: [*node, *node]\nname: valid\n")
    with pytest.raises(ValueError, match="Invalid Git collection"):
        git._collect_files("collections", {}, tmp_path, tmp_path, "collection.git")


@pytest.mark.parametrize("field", ["src", "name", "version"])  # type: ignore[untyped-decorator]
async def test_requirement_rejects_alias_graph_before_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """Structured source and version values fail before network access.

    Args:
        tmp_path: Disposable project directory.
        monkeypatch: Isolated database and fetch overrides.
        field: Field containing an aliased YAML graph.
    """
    declaration = "  - type: git\n"
    if field == "version":
        declaration += "    name: https://git.example/collection.git\n"
    declaration += f"    {field}: *graph\n"
    (tmp_path / "requirements.yml").write_text("graph: &graph [a, a]\ncollections:\n" + declaration)
    db = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                SimpleNamespace(enabled=True),
                SimpleNamespace(requirements_token=None, scm_token=None, scm_provider=None),
            ]
        )
    )

    @asynccontextmanager
    async def session():  # type: ignore[no-untyped-def]
        yield db

    class AliasGraph(list[object]):
        def __str__(self) -> str:
            raise AssertionError("Structured requirements must not be stringified")

    declarations = git._read_declarations(tmp_path)
    declarations[0][1][field] = AliasGraph(["a", "a"])
    monkeypatch.setattr(git, "_read_declarations", lambda _: declarations)
    snapshot = AsyncMock()
    monkeypatch.setattr(git, "get_session", session)
    monkeypatch.setattr(git, "git_snapshot", snapshot)
    with pytest.raises(ValueError, match="source must be a string|Invalid Git requirement version"):
        await git.resolve_git_requirements("p", str(tmp_path), repo_url="https://git.example/project")
    snapshot.assert_not_called()


@pytest.mark.parametrize("value", [["role"], {"role": "name"}])  # type: ignore[untyped-decorator]
def test_role_name_rejects_structured_values(tmp_path: Path, value: object) -> None:
    """Role aliases cannot expand during install name conversion.

    Args:
        tmp_path: Disposable Git checkout.
        value: Non-scalar role name.
    """
    with pytest.raises(ValueError, match="Invalid Git role name"):
        git._collect_files("roles", {"name": value}, tmp_path, tmp_path, "role.git")
