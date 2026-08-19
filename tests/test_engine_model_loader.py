"""Tests for apme_engine.engine.model_loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from apme_engine.engine.model_loader import (
    _safe_int,
    load_collection,
    load_file,
    load_play,
    load_playbook,
    load_playbooks,
    load_requirements,
    load_role,
    load_roleinplay,
    load_roles,
    load_task,
    load_taskfile,
)
from apme_engine.engine.models import (
    Collection,
    File,
    Play,
    Playbook,
    PlaybookFormatError,
    Role,
    RoleInPlay,
    Task,
    TaskFile,
    YAMLDict,
)

SIMPLE_PLAYBOOK_YAML = (
    "---\n"
    "- name: Test play\n"
    "  hosts: localhost\n"
    "  tasks:\n"
    "    - name: Debug\n"
    "      ansible.builtin.debug:\n"
    "        msg: hello\n"
)

SIMPLE_TASKFILE_YAML = "---\n- name: Copy file\n  ansible.builtin.copy:\n    src: a.txt\n    dest: /tmp/a.txt\n"


class TestSafeInt:
    """Tests for _safe_int."""

    def test_int(self) -> None:
        """Integer input returns as-is."""
        assert _safe_int(42) == 42

    def test_float(self) -> None:
        """Float input truncates to int."""
        assert _safe_int(3.7) == 3

    def test_str_valid(self) -> None:
        """Valid numeric string converts to int."""
        assert _safe_int("10") == 10

    def test_str_invalid(self) -> None:
        """Invalid string returns 0."""
        assert _safe_int("abc") == 0

    def test_none(self) -> None:
        """None returns 0."""
        assert _safe_int(None) == 0

    def test_list(self) -> None:
        """List returns 0."""
        assert _safe_int([1, 2]) == 0


class TestLoadPlaybook:
    """Tests for load_playbook."""

    def test_from_yaml_str(self) -> None:
        """Load playbook from YAML string."""
        pb = load_playbook(yaml_str=SIMPLE_PLAYBOOK_YAML)
        assert isinstance(pb, Playbook)
        assert pb.type == "playbook"
        assert len(pb.plays) > 0

    def test_from_file(self, tmp_path: Path) -> None:
        """Load playbook from file path.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        pb_file = tmp_path / "play.yml"
        pb_file.write_text(SIMPLE_PLAYBOOK_YAML)
        pb = load_playbook(path="play.yml", basedir=str(tmp_path))
        assert isinstance(pb, Playbook)
        assert len(pb.plays) > 0

    def test_multi_play(self) -> None:
        """Load playbook with multiple plays."""
        yaml_str = (
            "---\n"
            "- name: Play 1\n"
            "  hosts: web\n"
            "  tasks:\n"
            "    - name: Task 1\n"
            "      ansible.builtin.debug:\n"
            "        msg: play1\n"
            "\n"
            "- name: Play 2\n"
            "  hosts: db\n"
            "  tasks:\n"
            "    - name: Task 2\n"
            "      ansible.builtin.debug:\n"
            "        msg: play2\n"
        )
        pb = load_playbook(yaml_str=yaml_str)
        assert len(pb.plays) == 2

    def test_empty_yaml(self) -> None:
        """Empty YAML returns playbook with no plays."""
        pb = load_playbook(yaml_str="---\n")
        assert isinstance(pb, Playbook)
        assert len(pb.plays) == 0

    def test_malformed_yaml_raises(self) -> None:
        """Malformed YAML raises PlaybookFormatError when not skipped."""
        yaml_str = "---\nnot_a_playbook:\n  key: value\n"
        with pytest.raises(PlaybookFormatError):
            load_playbook(yaml_str=yaml_str, skip_playbook_format_error=False)


class TestLoadTaskfile:
    """Tests for load_taskfile."""

    def test_from_yaml_str(self) -> None:
        """Load taskfile from YAML string."""
        tf = load_taskfile(path="tasks/main.yml", yaml_str=SIMPLE_TASKFILE_YAML)
        assert isinstance(tf, TaskFile)
        assert len(tf.tasks) > 0

    def test_from_file(self, tmp_path: Path) -> None:
        """Load taskfile from file path.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        tf_file = tmp_path / "tasks.yml"
        tf_file.write_text(SIMPLE_TASKFILE_YAML)
        tf = load_taskfile(path="tasks.yml", basedir=str(tmp_path))
        assert isinstance(tf, TaskFile)

    def test_empty_taskfile(self) -> None:
        """Empty taskfile has no tasks."""
        tf = load_taskfile(path="empty.yml", yaml_str="---\n")
        assert isinstance(tf, TaskFile)
        assert len(tf.tasks) == 0


class TestLoadPlay:
    """Tests for load_play."""

    def test_basic_play(self) -> None:
        """Load play with tasks."""
        play_dict: YAMLDict = {
            "name": "My play",
            "hosts": "localhost",
            "gather_facts": False,
            "tasks": [
                {"name": "Debug task", "ansible.builtin.debug": {"msg": "hello"}},
            ],
        }
        play = load_play(
            path="play.yml",
            index=0,
            play_block_dict=play_dict,
            yaml_lines=SIMPLE_PLAYBOOK_YAML,
        )
        assert isinstance(play, Play)
        assert play.name == "My play"
        assert len(play.tasks) > 0

    def test_play_with_roles(self) -> None:
        """Load play with roles."""
        play_dict: YAMLDict = {
            "name": "Role play",
            "hosts": "all",
            "roles": [{"role": "common"}],
            "tasks": [],
        }
        play = load_play(path="pb.yml", index=0, play_block_dict=play_dict, yaml_lines="---\n")
        assert isinstance(play, Play)
        assert len(play.roles) > 0

    def test_play_with_become(self) -> None:
        """Load play with become options."""
        play_dict: YAMLDict = {
            "name": "Privileged play",
            "hosts": "all",
            "become": True,
            "become_user": "root",
            "tasks": [],
        }
        play = load_play(path="pb.yml", index=0, play_block_dict=play_dict, yaml_lines="---\n")
        assert play.options.get("become") is True

    def test_play_with_pre_and_post_tasks(self) -> None:
        """Load play with pre_tasks and post_tasks."""
        play_dict: YAMLDict = {
            "name": "Multi-section play",
            "hosts": "all",
            "pre_tasks": [
                {"name": "Pre task", "ansible.builtin.debug": {"msg": "pre"}},
            ],
            "tasks": [],
            "post_tasks": [
                {"name": "Post task", "ansible.builtin.debug": {"msg": "post"}},
            ],
        }
        yaml_lines = (
            "---\n- name: Multi-section play\n  hosts: all\n  pre_tasks:\n"
            "    - name: Pre task\n      ansible.builtin.debug:\n        msg: pre\n"
            "  tasks: []\n  post_tasks:\n    - name: Post task\n"
            "      ansible.builtin.debug:\n        msg: post\n"
        )
        play = load_play(path="pb.yml", index=0, play_block_dict=play_dict, yaml_lines=yaml_lines)
        assert isinstance(play, Play)
        assert len(play.pre_tasks) > 0
        assert len(play.post_tasks) > 0

    def test_play_with_handlers(self) -> None:
        """Load play with handlers."""
        play_dict: YAMLDict = {
            "name": "Handler play",
            "hosts": "all",
            "tasks": [],
            "handlers": [
                {"name": "restart svc", "ansible.builtin.service": {"name": "svc", "state": "restarted"}},
            ],
        }
        yaml_lines = (
            "---\n- name: Handler play\n  hosts: all\n  tasks: []\n  handlers:\n"
            "    - name: restart svc\n      ansible.builtin.service:\n"
            "        name: svc\n        state: restarted\n"
        )
        play = load_play(path="pb.yml", index=0, play_block_dict=play_dict, yaml_lines=yaml_lines)
        assert len(play.handlers) > 0


class TestLoadTask:
    """Tests for load_task."""

    def test_basic_task(self, tmp_path: Path) -> None:
        """Load task with module and name.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        task_dict: dict[str, object] = {
            "name": "Install package",
            "ansible.builtin.package": {"name": "vim", "state": "present"},
        }
        tf_content = "---\n- name: Install package\n  ansible.builtin.package:\n    name: vim\n    state: present\n"
        task = load_task(
            path="tasks/main.yml",
            index=0,
            task_block_dict=task_dict,
            yaml_lines=tf_content,
        )
        assert isinstance(task, Task)
        assert task.name == "Install package"
        assert "ansible.builtin.package" in task.module

    def test_task_with_register(self) -> None:
        """Load task with register."""
        task_dict: dict[str, object] = {
            "name": "Run command",
            "ansible.builtin.shell": "echo hello",
            "register": "result",
        }
        yaml_lines = "---\n- name: Run command\n  ansible.builtin.shell: echo hello\n  register: result\n"
        task = load_task(path="tasks/main.yml", index=0, task_block_dict=task_dict, yaml_lines=yaml_lines)
        assert isinstance(task, Task)

    def test_task_with_loop(self) -> None:
        """Load task with loop."""
        task_dict: dict[str, object] = {
            "name": "Loop task",
            "ansible.builtin.debug": {"msg": "{{ item }}"},
            "loop": ["a", "b", "c"],
        }
        yaml_lines = (
            "---\n- name: Loop task\n  ansible.builtin.debug:\n    msg: '{{ item }}'\n"
            "  loop:\n    - a\n    - b\n    - c\n"
        )
        task = load_task(path="tasks/main.yml", index=0, task_block_dict=task_dict, yaml_lines=yaml_lines)
        assert isinstance(task, Task)

    def test_task_file_not_found_raises(self) -> None:
        """Load task with nonexistent path raises ValueError."""
        task_dict: dict[str, object] = {"name": "test", "ansible.builtin.debug": {"msg": "hi"}}
        with pytest.raises(ValueError, match="file not found"):
            load_task(path="nonexistent.yml", index=0, task_block_dict=task_dict)

    def test_task_with_block(self) -> None:
        """Load task with block preserves children as Task objects."""
        task_dict: dict[str, object] = {
            "block": [
                {"name": "Inner task", "ansible.builtin.debug": {"msg": "inside block"}},
            ],
        }
        yaml_lines = "---\n- block:\n    - name: Inner task\n      ansible.builtin.debug:\n        msg: inside block\n"
        task = load_task(path="tasks/main.yml", index=0, task_block_dict=task_dict, yaml_lines=yaml_lines)
        assert isinstance(task, Task)
        assert task.module == ""
        block_children = task.options.get("block")
        assert isinstance(block_children, list)
        assert len(block_children) == 1
        assert isinstance(block_children[0], Task)
        assert block_children[0].name == "Inner task"

    def test_block_with_rescue_always(self) -> None:
        """Block with rescue and always sections loads all child tasks."""
        task_dict: dict[str, object] = {
            "name": "Migration block",
            "block": [
                {"name": "Migrate", "ansible.builtin.command": "migrate.sh"},
            ],
            "rescue": [
                {"name": "Rollback", "ansible.builtin.command": "rollback.sh"},
            ],
            "always": [
                {"name": "Report", "ansible.builtin.debug": {"msg": "done"}},
            ],
        }
        yaml_lines = (
            "---\n- name: Migration block\n  block:\n"
            "    - name: Migrate\n      ansible.builtin.command: migrate.sh\n"
            "  rescue:\n    - name: Rollback\n      ansible.builtin.command: rollback.sh\n"
            "  always:\n    - name: Report\n      ansible.builtin.debug:\n        msg: done\n"
        )
        task = load_task(path="tasks/main.yml", index=0, task_block_dict=task_dict, yaml_lines=yaml_lines)
        assert task.module == ""
        assert task.name == "Migration block"
        block_tasks = task.options["block"]
        rescue_tasks = task.options["rescue"]
        always_tasks = task.options["always"]
        assert isinstance(block_tasks, list) and len(block_tasks) == 1
        assert isinstance(rescue_tasks, list) and len(rescue_tasks) == 1
        assert isinstance(always_tasks, list) and len(always_tasks) == 1
        assert isinstance(block_tasks[0], Task) and block_tasks[0].name == "Migrate"
        assert isinstance(rescue_tasks[0], Task) and rescue_tasks[0].name == "Rollback"
        assert isinstance(always_tasks[0], Task) and always_tasks[0].name == "Report"

    def test_block_preserves_inherited_properties(self) -> None:
        """Block-level when/become/tags are on the block Task, not lost."""
        task_dict: dict[str, object] = {
            "name": "Privileged block",
            "become": True,
            "become_user": "root",
            "when": "should_run",
            "tags": ["deploy"],
            "block": [
                {"name": "Inner", "ansible.builtin.debug": {"msg": "hi"}},
            ],
        }
        yaml_lines = (
            "---\n- name: Privileged block\n  become: true\n  become_user: root\n"
            "  when: should_run\n  tags: [deploy]\n  block:\n"
            "    - name: Inner\n      ansible.builtin.debug:\n        msg: hi\n"
        )
        task = load_task(path="tasks/main.yml", index=0, task_block_dict=task_dict, yaml_lines=yaml_lines)
        assert task.options["become"] is True
        assert task.options["become_user"] == "root"
        assert task.options["when"] == "should_run"
        assert task.options["tags"] == ["deploy"]

    def test_nested_blocks(self) -> None:
        """Nested block inside a block produces nested Task structure."""
        task_dict: dict[str, object] = {
            "name": "Outer",
            "block": [
                {
                    "name": "Inner block",
                    "block": [
                        {"name": "Leaf", "ansible.builtin.debug": {"msg": "deep"}},
                    ],
                },
            ],
        }
        yaml_lines = (
            "---\n- name: Outer\n  block:\n    - name: Inner block\n"
            "      block:\n        - name: Leaf\n          ansible.builtin.debug:\n            msg: deep\n"
        )
        task = load_task(path="tasks/main.yml", index=0, task_block_dict=task_dict, yaml_lines=yaml_lines)
        assert task.module == ""
        outer_block = task.options["block"]
        assert isinstance(outer_block, list)
        inner = outer_block[0]
        assert isinstance(inner, Task)
        assert inner.module == ""
        assert inner.name == "Inner block"
        leaf_children = inner.options.get("block")
        assert isinstance(leaf_children, list)
        leaf = leaf_children[0]
        assert isinstance(leaf, Task)
        assert leaf.name == "Leaf"


class TestLoadRoleInPlay:
    """Tests for load_roleinplay."""

    def test_basic_role(self) -> None:
        """Load role with name only."""
        rip = load_roleinplay(
            name="common",
            options={},
            defined_in="pb.yml",
            role_index=0,
            play_index=0,
        )
        assert isinstance(rip, RoleInPlay)
        assert rip.name == "common"

    def test_role_with_options(self) -> None:
        """Load role with options."""
        rip = load_roleinplay(
            name="webserver",
            options={"port": 8080, "ssl": True},
            defined_in="pb.yml",
            role_index=1,
            play_index=0,
        )
        assert rip.name == "webserver"


class TestLoadFile:
    """Tests for load_file."""

    def test_load_with_body(self) -> None:
        """Load file with explicit body."""
        f = load_file(path="vars/main.yml", body="key: value\n", read=False)
        assert isinstance(f, File)
        assert f.body == "key: value\n"

    def test_load_with_read_false(self) -> None:
        """Load with read=False uses empty body for nonexistent."""
        f = load_file(path="nonexistent.yml", read=False)
        assert isinstance(f, File)
        assert f.body == ""

    def test_load_from_disk(self, tmp_path: Path) -> None:
        """Load file from disk when basedir and path provided.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        fpath = tmp_path / "data.yml"
        fpath.write_text("key: value\n")
        f = load_file(path="data.yml", basedir=str(tmp_path))
        assert isinstance(f, File)
        assert "key: value" in f.body

    def test_load_yaml_date_tag_is_json_serializable(self) -> None:
        """YAML date tags must not break File.data JSON serialization."""
        import json

        f = load_file(path="vars/main.yml", body="released: 2024-01-15\n", read=False)
        assert isinstance(f.data, str)
        json.loads(f.data)


class TestLoadRequirements:
    """Tests for load_requirements."""

    def test_load_requirements_file(self, tmp_path: Path) -> None:
        """Load requirements.yml returns dict with collections.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        req_content = "---\ncollections:\n  - name: ansible.utils\nroles:\n  - src: geerlingguy.docker\n"
        req_file = tmp_path / "requirements.yml"
        req_file.write_text(req_content)
        result = load_requirements(str(tmp_path))
        assert isinstance(result, dict)
        assert "collections" in result

    def test_load_nonexistent_requirements(self, tmp_path: Path) -> None:
        """Load from nonexistent directory returns empty dict.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        result = load_requirements(str(tmp_path / "missing_dir"))
        assert result == {}


# ---------------------------------------------------------------------------
# Source-tree collection helpers
# ---------------------------------------------------------------------------

GALAXY_YML_CONTENT = (
    "namespace: testns\n"
    "name: testcol\n"
    "version: 1.0.0\n"
    "readme: README.md\n"
    "dependencies:\n"
    "  ansible.utils: '>=2.0.0'\n"
    "repository: https://github.com/example/testcol\n"
)


def _make_source_collection(tmp_path: Path, *, galaxy_yml: bool = True) -> Path:
    """Create a minimal source-tree collection layout under ``tmp_path``.

    Returns the collection root (``tmp_path/testns/testcol``).

    Args:
        tmp_path: Pytest temporary directory fixture.
        galaxy_yml: Whether to write a ``galaxy.yml`` file.

    Returns:
        Path to the collection root directory.
    """
    col_root = tmp_path / "testns" / "testcol"
    col_root.mkdir(parents=True)
    if galaxy_yml:
        (col_root / "galaxy.yml").write_text(GALAXY_YML_CONTENT)
    (col_root / "README.md").write_text("# Test Collection\n")
    (col_root / "LICENSE").write_text("MIT\n")
    plugins_dir = col_root / "plugins" / "modules"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "my_module.py").write_text("def main(): pass\n")
    meta_dir = col_root / "meta"
    meta_dir.mkdir()
    (meta_dir / "runtime.yml").write_text("requires_ansible: '>=2.14'\n")
    roles_dir = col_root / "roles" / "myrole" / "tasks"
    roles_dir.mkdir(parents=True)
    (roles_dir / "main.yml").write_text("---\n- name: Hello\n  ansible.builtin.debug:\n    msg: hi\n")
    return col_root


class TestLoadCollection:
    """Tests for load_collection on source-tree collections."""

    def test_galaxy_yml_populates_metadata(self, tmp_path: Path) -> None:
        """galaxy.yml is loaded as metadata when MANIFEST.json is absent.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path)
        coll = load_collection(str(col_root), basedir=str(tmp_path), load_children=False)
        assert isinstance(coll, Collection)
        assert isinstance(coll.metadata, dict)
        assert coll.metadata.get("namespace") == "testns"
        assert coll.metadata.get("name") == "testcol"
        assert coll.metadata.get("version") == "1.0.0"

    def test_galaxy_yml_populates_dependencies(self, tmp_path: Path) -> None:
        """Dependencies from galaxy.yml are wired into colObj.dependency.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path)
        coll = load_collection(str(col_root), basedir=str(tmp_path), load_children=False)
        deps = coll.dependency.get("collections", {})
        assert isinstance(deps, dict)
        assert "ansible.utils" in deps

    def test_manifest_json_takes_precedence(self, tmp_path: Path) -> None:
        """When MANIFEST.json exists, galaxy.yml is not loaded.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path)
        manifest = {
            "collection_info": {
                "namespace": "from_manifest",
                "name": "testcol",
                "version": "2.0.0",
                "dependencies": {},
            }
        }
        import json

        (col_root / "MANIFEST.json").write_text(json.dumps(manifest))
        coll = load_collection(str(col_root), basedir=str(tmp_path), load_children=False)
        ci = coll.metadata.get("collection_info", {})
        assert isinstance(ci, dict)
        assert ci.get("namespace") == "from_manifest"

    def test_directory_walk_populates_files(self, tmp_path: Path) -> None:
        """Collection files are discovered via directory walk when FILES.json is absent.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path)
        coll = load_collection(str(col_root), basedir=str(tmp_path), load_children=False)
        assert coll.files, "files should be populated from directory walk"
        raw_entries = coll.files.get("files", [])
        assert isinstance(raw_entries, list)
        names = [e["name"] for e in raw_entries if isinstance(e, dict)]
        assert "galaxy.yml" in names
        assert "README.md" in names
        assert "LICENSE" in names
        assert "plugins/modules/my_module.py" in names

    def test_files_json_takes_precedence(self, tmp_path: Path) -> None:
        """When FILES.json exists, directory walk is not used.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path)
        import json

        files_json = {"files": [{"name": "from_files_json.txt"}], "format": 1}
        (col_root / "FILES.json").write_text(json.dumps(files_json))
        coll = load_collection(str(col_root), basedir=str(tmp_path), load_children=False)
        raw_entries = coll.files.get("files", [])
        assert isinstance(raw_entries, list)
        names = [e["name"] for e in raw_entries if isinstance(e, dict)]
        assert "from_files_json.txt" in names
        assert "galaxy.yml" not in names

    def test_no_galaxy_yml_leaves_metadata_empty(self, tmp_path: Path) -> None:
        """Without galaxy.yml or MANIFEST.json, metadata stays empty.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path, galaxy_yml=False)
        coll = load_collection(str(col_root), basedir=str(tmp_path), load_children=False)
        assert not coll.metadata

    def test_meta_runtime_loaded(self, tmp_path: Path) -> None:
        """meta/runtime.yml is still loaded (unrelated to this change, sanity check).

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path)
        coll = load_collection(str(col_root), basedir=str(tmp_path), load_children=False)
        assert coll.meta_runtime
        assert coll.meta_runtime.get("requires_ansible") == ">=2.14"


class TestLoadPlaybooksNestedDirectories:
    """Tests for load_playbooks with arbitrary subdirectory structures."""

    def test_numbered_directories(self, tmp_path: Path) -> None:
        """Playbooks in numbered directories are discovered.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        # Create numbered directory structure
        (tmp_path / "01_first").mkdir()
        (tmp_path / "01_first" / "playbook.yml").write_text(SIMPLE_PLAYBOOK_YAML)
        (tmp_path / "02_second").mkdir()
        (tmp_path / "02_second" / "playbook.yml").write_text(SIMPLE_PLAYBOOK_YAML)

        playbooks = load_playbooks(str(tmp_path), basedir=str(tmp_path), load_children=False)

        assert len(playbooks) == 2
        assert any("01_first" in str(p) for p in playbooks)
        assert any("02_second" in str(p) for p in playbooks)

    def test_deep_nesting(self, tmp_path: Path) -> None:
        """Deeply nested playbooks are discovered.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        deep_dir = tmp_path / "level1" / "level2" / "level3"
        deep_dir.mkdir(parents=True)
        (deep_dir / "playbook.yml").write_text(SIMPLE_PLAYBOOK_YAML)

        playbooks = load_playbooks(str(tmp_path), basedir=str(tmp_path), load_children=False)

        assert len(playbooks) == 1
        assert "level1" in str(playbooks[0])
        assert "level2" in str(playbooks[0])
        assert "level3" in str(playbooks[0])

    def test_no_duplicates_from_multiple_patterns(self, tmp_path: Path) -> None:
        """Root-level playbooks aren't duplicated by recursive pattern.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "site.yml").write_text(SIMPLE_PLAYBOOK_YAML)
        (tmp_path / "playbooks").mkdir()
        (tmp_path / "playbooks" / "deploy.yml").write_text(SIMPLE_PLAYBOOK_YAML)

        playbooks = load_playbooks(str(tmp_path), basedir=str(tmp_path), load_children=False)

        # Should find both, no duplicates
        assert len(playbooks) == 2
        playbook_paths = [str(p) for p in playbooks]
        assert len(set(playbook_paths)) == 2  # All unique

    def test_roles_still_excluded(self, tmp_path: Path) -> None:
        """Playbooks inside roles/ are still excluded.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        roles_dir = tmp_path / "roles" / "common" / "tasks"
        roles_dir.mkdir(parents=True)
        # Even if it looks like a playbook, it's in roles/ so should be excluded
        (roles_dir / "main.yml").write_text(SIMPLE_PLAYBOOK_YAML)

        playbooks = load_playbooks(str(tmp_path), basedir=str(tmp_path), load_children=False)

        assert len(playbooks) == 0

    def test_non_playbook_yaml_excluded(self, tmp_path: Path) -> None:
        """YAML files that aren't playbooks are excluded.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "config").mkdir()
        # This is a vars file, not a playbook (no hosts/include/import_playbook)
        (tmp_path / "config" / "settings.yml").write_text("---\nkey: value\n")

        playbooks = load_playbooks(str(tmp_path), basedir=str(tmp_path), load_children=False)

        assert len(playbooks) == 0

    def test_mixed_structure(self, tmp_path: Path) -> None:
        """Mixed standard and numbered directory structure works.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        # Standard structure
        (tmp_path / "site.yml").write_text(SIMPLE_PLAYBOOK_YAML)
        (tmp_path / "playbooks").mkdir()
        (tmp_path / "playbooks" / "deploy.yml").write_text(SIMPLE_PLAYBOOK_YAML)

        # Numbered structure
        (tmp_path / "01_setup").mkdir()
        (tmp_path / "01_setup" / "setup.yml").write_text(SIMPLE_PLAYBOOK_YAML)

        # Roles (should be excluded)
        roles_dir = tmp_path / "roles" / "common" / "tasks"
        roles_dir.mkdir(parents=True)
        (roles_dir / "main.yml").write_text(SIMPLE_PLAYBOOK_YAML)

        playbooks = load_playbooks(str(tmp_path), basedir=str(tmp_path), load_children=False)

        assert len(playbooks) == 3
        playbook_strs = [str(p) for p in playbooks]
        assert any("site.yml" in p for p in playbook_strs)
        assert any("playbooks" in p and "deploy.yml" in p for p in playbook_strs)
        assert any("01_setup" in p and "setup.yml" in p for p in playbook_strs)
        assert not any("roles" in p for p in playbook_strs)

    def test_molecule_excluded_by_default(self, tmp_path: Path) -> None:
        """Molecule directory playbooks excluded when include_test_contents=False.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        # Create molecule directory with playbook
        molecule_dir = tmp_path / "molecule" / "default"
        molecule_dir.mkdir(parents=True)
        (molecule_dir / "converge.yml").write_text(SIMPLE_PLAYBOOK_YAML)

        # Also add a valid playbook outside molecule
        (tmp_path / "site.yml").write_text(SIMPLE_PLAYBOOK_YAML)

        playbooks = load_playbooks(str(tmp_path), basedir=str(tmp_path), load_children=False)

        assert len(playbooks) == 1
        assert "site.yml" in str(playbooks[0])
        assert not any("molecule" in str(p) for p in playbooks)

    def test_tests_excluded_by_default(self, tmp_path: Path) -> None:
        """Tests directory playbooks excluded when include_test_contents=False.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        # Create tests/integration directory with playbook
        tests_dir = tmp_path / "tests" / "integration"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test.yml").write_text(SIMPLE_PLAYBOOK_YAML)

        # Also add a valid playbook outside tests
        (tmp_path / "site.yml").write_text(SIMPLE_PLAYBOOK_YAML)

        playbooks = load_playbooks(str(tmp_path), basedir=str(tmp_path), load_children=False)

        assert len(playbooks) == 1
        assert "site.yml" in str(playbooks[0])
        assert not any("tests" in str(p) for p in playbooks)

    def test_tasks_directory_excluded(self, tmp_path: Path) -> None:
        """Task files in tasks/ directories not promoted to playbooks.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        # Create tasks directory with playbook-shaped YAML
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "main.yml").write_text(SIMPLE_PLAYBOOK_YAML)

        # Nested tasks directory
        nested_tasks = tmp_path / "playbooks" / "tasks"
        nested_tasks.mkdir(parents=True)
        (nested_tasks / "setup.yml").write_text(SIMPLE_PLAYBOOK_YAML)

        # Valid playbook outside tasks
        (tmp_path / "site.yml").write_text(SIMPLE_PLAYBOOK_YAML)

        playbooks = load_playbooks(str(tmp_path), basedir=str(tmp_path), load_children=False)

        assert len(playbooks) == 1
        assert "site.yml" in str(playbooks[0])
        assert not any("tasks" in str(p) for p in playbooks)


class TestLoadRoles:
    """Tests for load_roles directory discovery."""

    def test_skips_requirements_yml_file_under_roles(self, tmp_path: Path) -> None:
        """roles/requirements.yml as a file must not raise NotADirectoryError.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "requirements.yml").write_text("---\ncollections: []\n")

        real_role_tasks = roles_dir / "web" / "tasks"
        real_role_tasks.mkdir(parents=True)
        (real_role_tasks / "main.yml").write_text("---\n- name: Hello\n  ansible.builtin.debug:\n    msg: hi\n")

        roles = load_roles(str(tmp_path), basedir=str(tmp_path), load_children=False)

        assert len(roles) == 1
        assert "web" in str(roles[0])

    def test_skips_non_directory_under_test_sub_roles(self, tmp_path: Path) -> None:
        """Files under tests/.../roles/ must not be treated as role dirs.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        # Anchor top-level roles/ so discovery does not latch onto tests/.../roles.
        (tmp_path / "roles").mkdir()

        # Target has roles/ but no top-level tasks/ (hits the elif branch).
        sub_roles = tmp_path / "tests" / "integration" / "targets" / "demo" / "roles"
        sub_roles.mkdir(parents=True)
        (sub_roles / "requirements.yml").write_text("---\ncollections: []\n")

        real_role_tasks = sub_roles / "helper" / "tasks"
        real_role_tasks.mkdir(parents=True)
        (real_role_tasks / "main.yml").write_text("---\n- name: Hello\n  ansible.builtin.debug:\n    msg: hi\n")

        roles = load_roles(
            str(tmp_path),
            basedir=str(tmp_path),
            include_test_contents=True,
            load_children=False,
        )

        assert len(roles) == 1
        assert "helper" in str(roles[0])
        assert not any("requirements.yml" in str(r) for r in roles)


class TestLoadRoleArgumentSpecs:
    """Tests for standalone meta/argument_specs loading in load_role."""

    def _make_role_dir(self, tmp_path: Path) -> Path:
        role_dir = tmp_path / "myrole"
        tasks_dir = role_dir / "tasks"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "main.yml").write_text("---\n- name: Hello\n  ansible.builtin.debug:\n    msg: hi\n")
        return role_dir

    def test_loads_standalone_argument_specs_yml(self, tmp_path: Path) -> None:
        """Standalone ``meta/argument_specs.yml`` merges into role metadata.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        role_dir = self._make_role_dir(tmp_path)
        meta_dir = role_dir / "meta"
        meta_dir.mkdir()
        (meta_dir / "main.yml").write_text("---\ngalaxy_info:\n  author: me\ndependencies: []\n")
        (meta_dir / "argument_specs.yml").write_text(
            "---\nmain:\n  short_description: Role description.\n  options: {}\n"
        )

        role = load_role(str(role_dir), basedir=str(tmp_path), load_children=False)

        assert isinstance(role, Role)
        assert role.metadata is not None
        assert role.metadata.get("argument_specs") == {
            "main": {"short_description": "Role description.", "options": {}},
        }

    def test_loads_standalone_argument_specs_yaml(self, tmp_path: Path) -> None:
        """Standalone ``meta/argument_specs.yaml`` merges into role metadata.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        role_dir = self._make_role_dir(tmp_path)
        meta_dir = role_dir / "meta"
        meta_dir.mkdir()
        (meta_dir / "main.yml").write_text("---\ngalaxy_info:\n  author: me\ndependencies: []\n")
        (meta_dir / "argument_specs.yaml").write_text(
            "---\nargument_specs:\n  main:\n    short_description: Role description.\n"
        )

        role = load_role(str(role_dir), basedir=str(tmp_path), load_children=False)

        assert isinstance(role, Role)
        assert role.metadata is not None
        assert role.metadata.get("argument_specs") == {"main": {"short_description": "Role description."}}

    def test_ignores_malformed_standalone_argument_specs(self, tmp_path: Path) -> None:
        """Malformed standalone argument_specs files are not merged into metadata.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        role_dir = self._make_role_dir(tmp_path)
        meta_dir = role_dir / "meta"
        meta_dir.mkdir()
        (meta_dir / "main.yml").write_text("---\ngalaxy_info:\n  author: me\ndependencies: []\n")
        (meta_dir / "argument_specs.yml").write_text("invalid\n")

        role = load_role(str(role_dir), basedir=str(tmp_path), load_children=False)

        assert isinstance(role, Role)
        assert role.metadata is not None
        assert "argument_specs" not in role.metadata

    def test_ignores_standalone_galaxy_info_mapping(self, tmp_path: Path) -> None:
        """Standalone files with only galaxy_info are not treated as argument_specs.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        role_dir = self._make_role_dir(tmp_path)
        meta_dir = role_dir / "meta"
        meta_dir.mkdir()
        (meta_dir / "main.yml").write_text("---\ngalaxy_info:\n  author: me\ndependencies: []\n")
        (meta_dir / "argument_specs.yml").write_text("---\ngalaxy_info:\n  author: me\n")

        role = load_role(str(role_dir), basedir=str(tmp_path), load_children=False)

        assert isinstance(role, Role)
        assert role.metadata is not None
        assert "argument_specs" not in role.metadata

    def test_ignores_standalone_invalid_entry_point(self, tmp_path: Path) -> None:
        """Standalone files with scalar entry-point values are not merged.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        role_dir = self._make_role_dir(tmp_path)
        meta_dir = role_dir / "meta"
        meta_dir.mkdir()
        (meta_dir / "main.yml").write_text("---\ngalaxy_info:\n  author: me\ndependencies: []\n")
        (meta_dir / "argument_specs.yml").write_text("---\nargument_specs:\n  main: invalid\n")

        role = load_role(str(role_dir), basedir=str(tmp_path), load_children=False)

        assert isinstance(role, Role)
        assert role.metadata is not None
        assert "argument_specs" not in role.metadata
