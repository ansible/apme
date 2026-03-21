"""Tests for apme_engine.engine.utils."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from apme_engine.engine.models import YAMLDict
from apme_engine.engine.utils import (
    bool_values,
    bool_values_false,
    bool_values_true,
    equal,
    escape_local_path,
    escape_url,
    get_class_by_arg_type,
    get_collection_metadata,
    get_hash_of_url,
    get_lock_file_name,
    get_role_metadata,
    indent,
    is_local_path,
    is_test_object,
    is_url,
    lock_file,
    parse_bool,
    recursive_copy_dict,
    remove_lock_file,
    report_to_display,
    split_name_and_version,
    split_target_playbook_fullpath,
    split_target_taskfile_fullpath,
    unlock_file,
)


class TestGetLockFileName:
    """Tests for get_lock_file_name path appending."""

    def test_appends_lock_extension(self) -> None:
        """Verifies get_lock_file_name appends .lock to path."""
        assert get_lock_file_name("/tmp/foo.json") == "/tmp/foo.json.lock"

    def test_empty_path(self) -> None:
        """Verifies empty path returns .lock."""
        assert get_lock_file_name("") == ".lock"


class TestLockUnlockRemove:
    """Tests for lock_file, unlock_file, and remove_lock_file."""

    def test_lock_file_returns_none_for_empty(self) -> None:
        """Verifies lock_file returns None for None or empty path."""
        assert lock_file(None) is None
        assert lock_file("") is None

    def test_lock_and_unlock(self, tmp_path: Path) -> None:
        """Verifies lock_file creates .lock and unlock_file releases it.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        fpath = str(tmp_path / "data.json")
        Path(fpath).write_text("{}")
        lk = lock_file(fpath)
        assert lk is not None
        assert os.path.exists(fpath + ".lock")
        unlock_file(lk)

    def test_unlock_non_filelock(self) -> None:
        """Verifies unlock_file handles non-FileLock or None gracefully."""
        unlock_file("not-a-lock")
        unlock_file(None)

    def test_remove_lock_file(self, tmp_path: Path) -> None:
        """Verifies remove_lock_file deletes .lock file after unlock.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        fpath = str(tmp_path / "data.json")
        Path(fpath).write_text("{}")
        lk = lock_file(fpath)
        assert lk is not None
        unlock_file(lk)
        remove_lock_file(lk)
        assert not os.path.exists(fpath + ".lock")

    def test_remove_lock_file_none(self) -> None:
        """Verifies remove_lock_file handles None gracefully."""
        remove_lock_file(None)


class TestGetCollectionMetadata:
    """Tests for get_collection_metadata MANIFEST.json reading."""

    def test_nonexistent_path(self) -> None:
        """Verifies returns None for nonexistent path."""
        assert get_collection_metadata("/nonexistent/path") is None

    def test_reads_manifest(self, tmp_path: Path) -> None:
        """Verifies reads MANIFEST.json and returns parsed dict.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        manifest = {"collection_info": {"name": "testcol", "namespace": "testns"}}
        (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
        result = get_collection_metadata(str(tmp_path))
        assert result == manifest

    def test_no_manifest(self, tmp_path: Path) -> None:
        """Verifies returns None when MANIFEST.json not present.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        result = get_collection_metadata(str(tmp_path))
        assert result is None


class TestGetRoleMetadata:
    """Tests for get_role_metadata meta/main.yml reading."""

    def test_nonexistent_path(self) -> None:
        """Verifies returns None for nonexistent path."""
        assert get_role_metadata("/nonexistent/path") is None

    def test_reads_meta_main(self, tmp_path: Path) -> None:
        """Verifies reads meta/main.yml and returns galaxy_info.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        meta_dir = tmp_path / "meta"
        meta_dir.mkdir()
        (meta_dir / "main.yml").write_text("galaxy_info:\n  author: tester\ndependencies: []\n")
        result = get_role_metadata(str(tmp_path))
        assert result is not None
        galaxy_info = result.get("galaxy_info")
        assert isinstance(galaxy_info, dict) and galaxy_info.get("author") == "tester"

    def test_no_meta_dir(self, tmp_path: Path) -> None:
        """Verifies returns None when meta directory not present.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        result = get_role_metadata(str(tmp_path))
        assert result is None


class TestEscapeUrl:
    """Tests for escape_url URL-to-filename conversion."""

    def test_basic(self) -> None:
        """Verifies slashes and colons replaced with underscores."""
        assert escape_url("https://example.com/path/to/file") == "https__example.com_path_to_file"

    def test_with_query(self) -> None:
        """Verifies query string stripped from result."""
        assert escape_url("https://example.com/file?key=val") == "https__example.com_file"


class TestEscapeLocalPath:
    """Tests for escape_local_path path-to-filename conversion."""

    def test_replaces_slashes(self) -> None:
        """Verifies slashes replaced with double underscores."""
        assert escape_local_path("/home/user/project") == "__home__user__project"


class TestGetHashOfUrl:
    """Tests for get_hash_of_url SHA-256 of response content."""

    def test_hashes_response(self) -> None:
        """Verifies returns 64-char hex digest of URL content."""
        mock_response = MagicMock()
        mock_response.content = b"test content"
        with patch("apme_engine.engine.utils.httpx.get", return_value=mock_response):
            result = get_hash_of_url("https://example.com/file.tar.gz")
        assert len(result) == 64  # SHA-256 hex digest


class TestSplitNameAndVersion:
    """Tests for split_name_and_version name:version parsing."""

    def test_with_version(self) -> None:
        """Verifies splits ns.col:1.0.0 into name and version."""
        assert split_name_and_version("ns.col:1.0.0") == ("ns.col", "1.0.0")

    def test_without_version(self) -> None:
        """Verifies returns empty version when no colon."""
        assert split_name_and_version("ns.col") == ("ns.col", "")

    def test_empty(self) -> None:
        """Verifies empty string returns empty tuple."""
        assert split_name_and_version("") == ("", "")


class TestSplitTargetPlaybookFullpath:
    """Tests for split_target_playbook_fullpath basedir and playbook extraction."""

    def test_with_playbooks_dir(self) -> None:
        """Verifies splits path with playbooks/ into basedir and playbook."""
        basedir, playbook = split_target_playbook_fullpath("/home/user/project/playbooks/site.yml")
        assert basedir == "/home/user/project"
        assert playbook == "playbooks/site.yml"

    def test_without_playbooks_dir(self) -> None:
        """Verifies splits path without playbooks/ uses parent as basedir."""
        basedir, playbook = split_target_playbook_fullpath("/home/user/project/site.yml")
        assert basedir == "/home/user/project"
        assert playbook == "site.yml"


class TestSplitTargetTaskfileFullpath:
    """Tests for split_target_taskfile_fullpath basedir and taskfile extraction."""

    def test_with_roles_dir(self) -> None:
        """Verifies splits path with roles/ into basedir and taskfile."""
        basedir, taskfile = split_target_taskfile_fullpath("/project/roles/myrole/tasks/main.yml")
        assert basedir == "/project"
        assert taskfile == "roles/myrole/tasks/main.yml"

    def test_without_roles_dir(self) -> None:
        """Verifies splits path without roles/ uses tasks parent as basedir."""
        basedir, taskfile = split_target_taskfile_fullpath("/project/tasks/main.yml")
        assert basedir == "/project/tasks"
        assert taskfile == "main.yml"

    def test_empty_result(self) -> None:
        """Verifies single path returns root and dir name."""
        basedir, taskfile = split_target_taskfile_fullpath("/onlydir")
        assert basedir == "/"
        assert taskfile == "onlydir"


class TestIsUrl:
    """Tests for is_url URL detection."""

    def test_http(self) -> None:
        """Verifies https URLs return True."""
        assert is_url("https://example.com") is True

    def test_not_url(self) -> None:
        """Verifies local paths return False."""
        assert is_url("some/path") is False

    def test_git_url(self) -> None:
        """Verifies git:// URLs return True."""
        assert is_url("git://github.com/repo") is True


class TestIsLocalPath:
    """Tests for is_local_path local path detection."""

    def test_url_returns_false(self) -> None:
        """Verifies URLs return False."""
        assert is_local_path("https://example.com") is False

    def test_path_with_slash(self) -> None:
        """Verifies paths starting with / return True."""
        assert is_local_path("/some/path") is True

    def test_existing_file(self, tmp_path: Path) -> None:
        """Verifies existing file path returns True.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        f = tmp_path / "test.yml"
        f.write_text("test")
        assert is_local_path(str(f)) is True


class TestIndent:
    """Tests for indent string utility."""

    def test_basic_indent(self) -> None:
        """Verifies indent adds spaces to each line."""
        result = indent("line1\nline2", level=4)
        assert result == "    line1\n    line2"

    def test_skips_empty_lines(self) -> None:
        """Verifies empty/whitespace-only lines skipped."""
        result = indent("line1\n   \nline2", level=2)
        assert result == "  line1\n  line2"

    def test_zero_indent(self) -> None:
        """Verifies level 0 returns unchanged string."""
        result = indent("line1\nline2", level=0)
        assert result == "line1\nline2"


class TestReportToDisplay:
    """Tests for report_to_display output formatting."""

    def test_no_content(self) -> None:
        """Verifies message when no playbooks or roles."""
        report: dict[str, object] = {"summary": {"playbooks": {"total": 0}, "roles": {"total": 0}}}
        result = report_to_display(report)
        assert "No playbooks and roles found" in result

    def test_with_playbooks(self) -> None:
        """Verifies playbook and role counts in output."""
        report: dict[str, object] = {
            "summary": {"playbooks": {"total": 3}, "roles": {"total": 2}},
            "details": [],
        }
        result = report_to_display(report)
        assert "3 playbooks" in result
        assert "2 roles" in result

    def test_with_details(self) -> None:
        """Verifies details content included in output."""
        report: dict[str, object] = {
            "summary": {"playbooks": {"total": 1}, "roles": {"total": 0}},
            "details": [{"results": [{"output": "Something found"}]}],
        }
        result = report_to_display(report)
        assert "Something found" in result


class TestGetClassByArgType:
    """Tests for get_class_by_arg_type type mapping."""

    @pytest.mark.parametrize(
        ("arg_type", "expected"),
        [
            ("str", str),
            ("list", list),
            ("dict", dict),
            ("bool", bool),
            ("int", int),
            ("float", float),
            ("path", str),
            ("raw", object),
            ("jsonarg", str),
            ("json", str),
            ("bytes", str),
            ("bits", str),
        ],
    )  # type: ignore[untyped-decorator]
    def test_known_types(self, arg_type: str, expected: type[object]) -> None:
        """Verifies get_class_by_arg_type returns correct Python type for known arg.

        Args:
            arg_type: Ansible module argument type string (e.g. str, list, dict).
            expected: Expected Python type for the given arg_type.

        """
        assert get_class_by_arg_type(arg_type) is expected

    def test_unknown_type(self) -> None:
        """Verifies unknown arg_type returns None."""
        assert get_class_by_arg_type("custom") is None

    def test_non_string(self) -> None:
        """Verifies non-string arg_type returns None."""
        assert get_class_by_arg_type(123) is None  # type: ignore[arg-type]


class TestEqual:
    """Tests for equal deep equality comparison."""

    def test_equal_dicts(self) -> None:
        """Verifies equal dicts return True."""
        assert equal({"a": 1, "b": 2}, {"a": 1, "b": 2}) is True

    def test_unequal_dicts(self) -> None:
        """Verifies unequal dict values return False."""
        assert equal({"a": 1}, {"a": 2}) is False

    def test_equal_lists(self) -> None:
        """Verifies equal lists return True."""
        assert equal([1, 2, 3], [1, 2, 3]) is True

    def test_unequal_lists_length(self) -> None:
        """Verifies different list lengths return False."""
        assert equal([1, 2], [1, 2, 3]) is False

    def test_unequal_lists_values(self) -> None:
        """Verifies different list values return False."""
        assert equal([1, 2], [1, 3]) is False

    def test_different_types(self) -> None:
        """Verifies different types return False."""
        assert equal(1, "1") is False

    def test_nested_dicts(self) -> None:
        """Verifies nested dict equality."""
        assert equal({"a": {"b": [1]}}, {"a": {"b": [1]}}) is True

    def test_objects_with_dict(self) -> None:
        """Verifies objects with __dict__ compared by dict contents."""

        class Obj:
            def __init__(self, x: int) -> None:
                self.x = x

        assert equal(Obj(1), Obj(1)) is True
        assert equal(Obj(1), Obj(2)) is False

    def test_primitives(self) -> None:
        """Verifies primitive equality for int and str."""
        assert equal(42, 42) is True
        assert equal(42, 43) is False
        assert equal("a", "a") is True


class TestRecursiveCopyDict:
    """Tests for recursive_copy_dict shallow and deep copy."""

    def test_shallow_copy(self) -> None:
        """Verifies top-level keys copied to dst."""
        src: YAMLDict = {"a": 1, "b": "hello"}
        dst: YAMLDict = {}
        recursive_copy_dict(src, dst)
        assert dst == {"a": 1, "b": "hello"}

    def test_nested_copy(self) -> None:
        """Verifies nested dicts are deep-copied (no shared references)."""
        src: YAMLDict = {"a": {"b": {"c": 42}}}
        dst: YAMLDict = {}
        recursive_copy_dict(src, dst)
        assert dst == {"a": {"b": {"c": 42}}}
        inner: YAMLDict = cast(YAMLDict, src["a"])
        inner_b: YAMLDict = cast(YAMLDict, inner["b"])
        inner_b["c"] = 99
        dst_a: YAMLDict = cast(YAMLDict, dst["a"])
        dst_b: YAMLDict = cast(YAMLDict, dst_a["b"])
        assert dst_b["c"] == 42

    def test_non_dict_src_raises(self) -> None:
        """Verifies ValueError when src is not dict."""
        with pytest.raises(ValueError, match="only dict"):
            recursive_copy_dict("not a dict", {})  # type: ignore[arg-type]

    def test_non_dict_dst_raises(self) -> None:
        """Verifies ValueError when dst is not dict."""
        with pytest.raises(ValueError, match="only dict"):
            recursive_copy_dict({}, "not a dict")  # type: ignore[arg-type]


class TestIsTestObject:
    """Tests for is_test_object detection of test paths."""

    def test_integration(self) -> None:
        """Verifies tests/integration/ path returns True."""
        assert is_test_object("tests/integration/foo.yml") is True

    def test_molecule(self) -> None:
        """Verifies molecule/ path returns True."""
        assert is_test_object("molecule/default/converge.yml") is True

    def test_regular_path(self) -> None:
        """Verifies regular playbook path returns False."""
        assert is_test_object("playbooks/site.yml") is False


class TestParseBool:
    """Tests for parse_bool with various input types."""

    @pytest.mark.parametrize("value", ["y", "yes", "on", "1", "true", "t", True, 1, 1.0])  # type: ignore[untyped-decorator]
    def test_truthy(self, value: object) -> None:
        """Verifies parse_bool returns True for truthy values.

        Args:
            value: Parametrized value to test (truthy string, bool, or number).

        """
        assert parse_bool(value) is True

    @pytest.mark.parametrize("value", ["n", "no", "off", "0", "false", "f", False, 0, 0.0])  # type: ignore[untyped-decorator]
    def test_falsy(self, value: object) -> None:
        """Verifies parse_bool returns False for falsy values.

        Args:
            value: Parametrized value to test (falsy string, bool, or number).

        """
        assert parse_bool(value) is False

    def test_invalid_raises(self) -> None:
        """Verifies parse_bool raises TypeError for invalid value."""
        with pytest.raises(TypeError, match="failed to parse"):
            parse_bool("maybe")

    def test_bytes_true(self) -> None:
        """Verifies bytes 'yes' parses to True."""
        assert parse_bool(b"yes") is True

    def test_bytes_false(self) -> None:
        """Verifies bytes 'no' parses to False."""
        assert parse_bool(b"no") is False

    def test_case_insensitive(self) -> None:
        """Verifies parse_bool is case insensitive."""
        assert parse_bool("YES") is True
        assert parse_bool("FALSE") is False

    def test_whitespace_stripped(self) -> None:
        """Verifies leading/trailing whitespace stripped before parse."""
        assert parse_bool("  true  ") is True


class TestBoolConstants:
    """Tests for bool_values_true, bool_values_false, bool_values."""

    def test_true_values(self) -> None:
        """Verifies bool_values_true contains True and 'yes'."""
        assert True in bool_values_true
        assert "yes" in bool_values_true

    def test_false_values(self) -> None:
        """Verifies bool_values_false contains False and 'no'."""
        assert False in bool_values_false
        assert "no" in bool_values_false

    def test_union(self) -> None:
        """Verifies bool_values is union of true and false sets."""
        assert bool_values == bool_values_true | bool_values_false


class TestGetDocumentationInModuleFile:
    """Tests for get_documentation_in_module_file DOCUMENTATION extraction."""

    def test_extracts_doc(self, tmp_path: Path) -> None:
        """Verifies extracts DOCUMENTATION string from module file.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        from apme_engine.engine.utils import get_documentation_in_module_file

        module = tmp_path / "mymod.py"
        module.write_text('DOCUMENTATION = """\nmodule: mymod\nshort_description: test\n"""\n')
        doc = get_documentation_in_module_file(str(module))
        assert "module: mymod" in doc

    def test_empty_path(self) -> None:
        """Verifies empty path returns empty string."""
        from apme_engine.engine.utils import get_documentation_in_module_file

        assert get_documentation_in_module_file("") == ""

    def test_nonexistent(self) -> None:
        """Verifies nonexistent file returns empty string."""
        from apme_engine.engine.utils import get_documentation_in_module_file

        assert get_documentation_in_module_file("/nonexistent/file.py") == ""
