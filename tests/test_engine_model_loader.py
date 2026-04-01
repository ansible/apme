"""Tests for apme_engine.engine.model_loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from apme_engine.engine.model_loader import (
    _safe_int,
    load_file,
    load_play,
    load_playbook,
    load_requirements,
    load_roleinplay,
    load_task,
    load_taskfile,
)
from apme_engine.engine.models import (
    File,
    Play,
    Playbook,
    PlaybookFormatError,
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
