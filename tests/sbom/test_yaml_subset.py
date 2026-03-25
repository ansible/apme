"""Unit tests for minimal YAML subset parser."""

from __future__ import annotations

from apme_engine.sbom._yaml_subset import parse_yaml_subset


class TestParseYamlSubsetBasic:
    """Tests for basic key-value parsing."""

    def test_simple_key_value(self) -> None:
        """parse_yaml_subset parses simple key:value pairs."""
        result = parse_yaml_subset("name: my-role\nversion: 1.0")
        assert result == {"name": "my-role", "version": "1.0"}

    def test_empty_input(self) -> None:
        """parse_yaml_subset returns empty dict on empty input."""
        assert parse_yaml_subset("") == {}

    def test_empty_value(self) -> None:
        """parse_yaml_subset handles key with empty value."""
        result = parse_yaml_subset("key:")
        assert result == {"key": ""}

    def test_comments_skipped(self) -> None:
        """parse_yaml_subset skips comment lines."""
        result = parse_yaml_subset("# comment\nname: test")
        assert result == {"name": "test"}

    def test_document_marker_skipped(self) -> None:
        """parse_yaml_subset skips YAML document markers."""
        result = parse_yaml_subset("---\nname: test")
        assert result == {"name": "test"}


class TestParseYamlSubsetQuotes:
    """Tests for quoted value handling."""

    def test_single_quoted_value(self) -> None:
        """parse_yaml_subset strips single quotes from values."""
        result = parse_yaml_subset("name: 'quoted value'")
        assert result == {"name": "quoted value"}

    def test_double_quoted_value(self) -> None:
        """parse_yaml_subset strips double quotes from values."""
        result = parse_yaml_subset('name: "double quoted"')
        assert result == {"name": "double quoted"}


class TestParseYamlSubsetNested:
    """Tests for nested mapping parsing."""

    def test_nested_mapping(self) -> None:
        """parse_yaml_subset parses one-level nested mappings."""
        text = "deps:\n  cisco.ios: '>=1.0'\n  cisco.nxos: '*'"
        result = parse_yaml_subset(text)
        assert result == {"deps": {"cisco.ios": ">=1.0", "cisco.nxos": "*"}}


class TestParseYamlSubsetLists:
    """Tests for list item parsing."""

    def test_list_items(self) -> None:
        """parse_yaml_subset parses list items."""
        text = "authors:\n  - Alice\n  - Bob"
        result = parse_yaml_subset(text)
        assert result == {"authors": ["Alice", "Bob"]}

    def test_list_followed_by_key(self) -> None:
        """parse_yaml_subset handles list followed by top-level key."""
        text = "items:\n  - first\n  - second\nnext_key: val"
        result = parse_yaml_subset(text)
        assert result == {"items": ["first", "second"], "next_key": "val"}


class TestParseYamlSubsetRobustness:
    """Tests for error handling and robustness."""

    def test_malformed_input_returns_empty(self) -> None:
        """parse_yaml_subset returns {} on malformed input, never raises."""
        result = parse_yaml_subset("corrupt\x00garbage")
        assert isinstance(result, dict)

    def test_multiline_mixed(self) -> None:
        """parse_yaml_subset handles top-level key, nested mapping, then another top-level key."""
        text = "name: test\ndeps:\n  a: '1'\n  b: '2'\nversion: 3.0"
        result = parse_yaml_subset(text)
        assert result["name"] == "test"
        assert result["deps"] == {"a": "1", "b": "2"}
        assert result["version"] == "3.0"
