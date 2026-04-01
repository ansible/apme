"""Unit tests for yaml_lines graph rules (Phase 2H)."""

from __future__ import annotations

from typing import cast

import pytest

from apme_engine.engine.content_graph import ContentGraph, ContentNode, EdgeType, NodeIdentity, NodeScope, NodeType
from apme_engine.engine.graph_scanner import scan
from apme_engine.engine.models import YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule
from apme_engine.validators.native.rules.L040_no_tabs_graph import NoTabsGraphRule
from apme_engine.validators.native.rules.L041_key_order_graph import KeyOrderGraphRule
from apme_engine.validators.native.rules.L043_deprecated_bare_vars_graph import DeprecatedBareVarsGraphRule
from apme_engine.validators.native.rules.L051_jinja_graph import JinjaGraphRule
from apme_engine.validators.native.rules.L060_line_length_graph import LineLengthGraphRule
from apme_engine.validators.native.rules.L073_indentation_graph import IndentationGraphRule
from apme_engine.validators.native.rules.L076_ansible_facts_bracket_graph import AnsibleFactsBracketGraphRule
from apme_engine.validators.native.rules.L078_dot_notation_graph import DotNotationGraphRule
from apme_engine.validators.native.rules.L083_hardcoded_group_graph import HardcodedGroupGraphRule
from apme_engine.validators.native.rules.L091_bool_filter_graph import BoolFilterGraphRule
from apme_engine.validators.native.rules.L094_dynamic_template_date_graph import DynamicTemplateDateGraphRule
from apme_engine.validators.native.rules.L098_yaml_key_duplicates_graph import YamlKeyDuplicatesGraphRule
from apme_engine.validators.native.rules.L099_yaml_quoted_strings_graph import YamlQuotedStringsGraphRule
from apme_engine.validators.native.rules.M014_top_level_fact_variables_graph import TopLevelFactVariablesGraphRule
from apme_engine.validators.native.rules.M015_play_hosts_magic_variable_graph import PlayHostsMagicVariableGraphRule
from apme_engine.validators.native.rules.M019_omap___pairs_yaml_tags_graph import OmapPairsYamlTagsGraphRule
from apme_engine.validators.native.rules.M020_vault_encrypted_tag_graph import VaultEncryptedTagGraphRule


def _make_task(
    *,
    module: str = "debug",
    module_options: YAMLDict | None = None,
    options: YAMLDict | None = None,
    name: str | None = None,
    yaml_lines: str = "",
    file_path: str = "site.yml",
    line_start: int = 10,
    path: str = "site.yml/plays[0]/tasks[0]",
) -> tuple[ContentGraph, str]:
    """Build a minimal playbook -> play -> task graph.

    Args:
        module: Module name as authored in YAML (short or FQCN).
        module_options: Module argument mapping.
        options: Task-level options (when, etc.).
        name: Optional task name.
        yaml_lines: Raw YAML source fragment for the task node.
        file_path: Source file path for the task.
        line_start: Starting line number.
        path: YAML path identity for the task node.

    Returns:
        Tuple of ``(graph, task_node_id)``.
    """
    g = ContentGraph()
    pb = ContentNode(
        identity=NodeIdentity(path="site.yml", node_type=NodeType.PLAYBOOK),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    task = ContentNode(
        identity=NodeIdentity(path=path, node_type=NodeType.TASK),
        file_path=file_path,
        line_start=line_start,
        name=name,
        module=module,
        module_options=module_options or {},
        options=options or {},
        yaml_lines=yaml_lines,
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(task)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, task.node_id, EdgeType.CONTAINS)
    return g, task.node_id


# ---------------------------------------------------------------------------
# L040 — NoTabs
# ---------------------------------------------------------------------------


class TestL040NoTabsGraphRule:
    """Tests for L040 NoTabsGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> NoTabsGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return NoTabsGraphRule()

    def test_violation_tab_in_yaml(self, rule: NoTabsGraphRule) -> None:
        """Violation when a line contains a tab character.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="name: x\n\t- bad\n")
        assert rule.match(g, tid) is True
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail.get("lines_with_tabs") == [2]

    def test_pass_no_tabs(self, rule: NoTabsGraphRule) -> None:
        """Pass when YAML uses spaces only.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="name: ok\n  debug:\n    msg: hi\n")
        assert rule.match(g, tid) is True
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L041 — KeyOrder
# ---------------------------------------------------------------------------


class TestL041KeyOrderGraphRule:
    """Tests for L041 KeyOrderGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> KeyOrderGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return KeyOrderGraphRule()

    def test_violation_module_before_name(self, rule: KeyOrderGraphRule) -> None:
        """Violation when ``name`` appears after the module key.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        yaml_text = "- debug:\n    msg: x\n  name: Task\n"
        g, tid = _make_task(module="debug", yaml_lines=yaml_text)
        assert rule.match(g, tid) is True
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert "name should appear before" in str(result.detail.get("message", ""))

    def test_pass_name_before_module(self, rule: KeyOrderGraphRule) -> None:
        """Pass when ``name`` precedes the module key.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        yaml_text = "- name: Task\n  debug:\n    msg: x\n"
        g, tid = _make_task(module="debug", yaml_lines=yaml_text)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_pass_no_name_key(self, rule: KeyOrderGraphRule) -> None:
        """Pass when there is no ``name`` key.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        yaml_text = "- debug:\n    msg: only\n"
        g, tid = _make_task(module="debug", yaml_lines=yaml_text)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_violation_fqcn_module_before_name(self, rule: KeyOrderGraphRule) -> None:
        """Violation when ``name`` appears after a FQCN module key.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        yaml_text = "- ansible.builtin.package:\n    state: present\n  name: Install\n"
        g, tid = _make_task(
            module="ansible.builtin.package",
            yaml_lines=yaml_text,
        )
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        keys = result.detail.get("keys_order")
        assert isinstance(keys, list)
        assert "ansible.builtin.package" in keys
        assert "name" in keys


# ---------------------------------------------------------------------------
# L060 — LineLength
# ---------------------------------------------------------------------------


class TestL060LineLengthGraphRule:
    """Tests for L060 LineLengthGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> LineLengthGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return LineLengthGraphRule()

    def test_violation_line_over_160(self, rule: LineLengthGraphRule) -> None:
        """Violation when any line exceeds 160 characters.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        long_line = "k: " + "x" * 158 + "\n"
        g, tid = _make_task(yaml_lines=long_line)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail.get("long_lines")

    def test_pass_short_lines(self, rule: LineLengthGraphRule) -> None:
        """Pass when all lines are within the limit.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="name: short\n  debug:\n    msg: ok\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L073 — Indentation
# ---------------------------------------------------------------------------


class TestL073IndentationGraphRule:
    """Tests for L073 IndentationGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> IndentationGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return IndentationGraphRule()

    def test_violation_odd_indent(self, rule: IndentationGraphRule) -> None:
        """Violation when leading space indent is not a multiple of two.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="   msg: odd\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        bad_lines = cast(list[int], result.detail.get("bad_indent_lines") or [])
        assert 1 in bad_lines

    def test_pass_even_indent(self, rule: IndentationGraphRule) -> None:
        """Pass when indentation uses multiples of two spaces.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="  msg: even\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L098 — YamlKeyDuplicates
# ---------------------------------------------------------------------------


class TestL098YamlKeyDuplicatesGraphRule:
    """Tests for L098 YamlKeyDuplicatesGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> YamlKeyDuplicatesGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return YamlKeyDuplicatesGraphRule()

    def test_violation_duplicate_key_same_indent(self, rule: YamlKeyDuplicatesGraphRule) -> None:
        """Violation when the same key appears twice at the same indent.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="name: first\nname: second\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert "duplicate" in str(result.detail.get("duplicates", [])).lower()

    def test_pass_unique_keys(self, rule: YamlKeyDuplicatesGraphRule) -> None:
        """Pass when mapping keys are unique at each indent.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="name: a\n  debug:\n    msg: x\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L099 — YamlQuotedStrings
# ---------------------------------------------------------------------------


class TestL099YamlQuotedStringsGraphRule:
    """Tests for L099 YamlQuotedStringsGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> YamlQuotedStringsGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return YamlQuotedStringsGraphRule()

    def test_violation_single_quoted_value(self, rule: YamlQuotedStringsGraphRule) -> None:
        """Violation for single-quoted scalar values.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="msg: 'hello'\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail.get("single_quoted_lines") == [1]

    def test_pass_double_quoted_value(self, rule: YamlQuotedStringsGraphRule) -> None:
        """Pass when values use double quotes or are unquoted.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines='msg: "hello"\n')
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# M019 — OmapPairsYamlTags
# ---------------------------------------------------------------------------


class TestM019OmapPairsYamlTagsGraphRule:
    """Tests for M019 OmapPairsYamlTagsGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> OmapPairsYamlTagsGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return OmapPairsYamlTagsGraphRule()

    def test_violation_omap_tag(self, rule: OmapPairsYamlTagsGraphRule) -> None:
        """Violation when deprecated ``!!omap`` appears.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="data: !!omap []\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        tags = cast(list[str], result.detail.get("tags") or [])
        assert "!!omap" in tags

    def test_pass_no_deprecated_tags(self, rule: OmapPairsYamlTagsGraphRule) -> None:
        """Pass when no ``!!omap`` / ``!!pairs`` tags are present.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="name: plain\n  debug:\n    msg: ok\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# M020 — VaultEncryptedTag
# ---------------------------------------------------------------------------


class TestM020VaultEncryptedTagGraphRule:
    """Tests for M020 VaultEncryptedTagGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> VaultEncryptedTagGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return VaultEncryptedTagGraphRule()

    def test_violation_vault_encrypted_tag(self, rule: VaultEncryptedTagGraphRule) -> None:
        """Violation when ``!vault-encrypted`` appears.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="secret: !vault-encrypted |\n  abc\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail.get("replacement") == "!vault"

    def test_pass_no_vault_encrypted(self, rule: VaultEncryptedTagGraphRule) -> None:
        """Pass when ``!vault-encrypted`` is absent.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="secret: !vault |\n  data\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L043 — DeprecatedBareVars
# ---------------------------------------------------------------------------


class TestL043DeprecatedBareVarsGraphRule:
    """Tests for L043 DeprecatedBareVarsGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> DeprecatedBareVarsGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return DeprecatedBareVarsGraphRule()

    def test_violation_bare_jinja_var(self, rule: DeprecatedBareVarsGraphRule) -> None:
        """Violation for bare ``{{ var }}`` without a filter.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines='msg: "{{ myvar }}"\n')
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        bare_vars = cast(list[str], result.detail.get("bare_vars") or [])
        assert "{{ myvar }}" in bare_vars

    def test_pass_jinja_with_filter(self, rule: DeprecatedBareVarsGraphRule) -> None:
        """Pass when Jinja uses a filter (non-bare form).

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="msg: \"{{ myvar | default('x') }}\"\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L051 — Jinja
# ---------------------------------------------------------------------------


class TestL051JinjaGraphRule:
    """Tests for L051 JinjaGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> JinjaGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return JinjaGraphRule()

    def test_violation_bad_spacing(self, rule: JinjaGraphRule) -> None:
        """Violation for cramped Jinja (e.g. missing spaces around ``|``).

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines='msg: "{{ foo|bar }}"\n')
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None

    def test_pass_well_spaced_jinja(self, rule: JinjaGraphRule) -> None:
        """Pass when Jinja spacing follows style guidance.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines='msg: "{{ foo | bar }}"\n')
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L076 — AnsibleFactsBracket
# ---------------------------------------------------------------------------


class TestL076AnsibleFactsBracketGraphRule:
    """Tests for L076 AnsibleFactsBracketGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> AnsibleFactsBracketGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return AnsibleFactsBracketGraphRule()

    def test_violation_injected_fact_name(self, rule: AnsibleFactsBracketGraphRule) -> None:
        """Violation when injected fact variables appear in raw YAML.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="when: ansible_distribution == 'RedHat'\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        found_facts = cast(list[str], result.detail.get("found_facts") or [])
        assert "ansible_distribution" in found_facts

    def test_pass_no_injected_facts(self, rule: AnsibleFactsBracketGraphRule) -> None:
        """Pass when injected fact names are not used as bare variables.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="when: ansible_facts['distribution'] == 'RedHat'\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L078 — DotNotation
# ---------------------------------------------------------------------------


class TestL078DotNotationGraphRule:
    """Tests for L078 DotNotationGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> DotNotationGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return DotNotationGraphRule()

    def test_violation_item_dot_access(self, rule: DotNotationGraphRule) -> None:
        """Violation for dot notation such as ``item.key``.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="msg: {{ item.name }}\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        patterns = cast(list[str], result.detail.get("found_patterns") or [])
        assert "item.name" in patterns

    def test_pass_bracket_notation(self, rule: DotNotationGraphRule) -> None:
        """Pass when dict access uses bracket notation in Jinja.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="msg: {{ item['name'] }}\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L083 — HardcodedGroup
# ---------------------------------------------------------------------------


class TestL083HardcodedGroupGraphRule:
    """Tests for L083 HardcodedGroupGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> HardcodedGroupGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return HardcodedGroupGraphRule()

    def test_violation_groups_in_role_path(self, rule: HardcodedGroupGraphRule) -> None:
        """Violation for ``groups['...']`` inside a role task file.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        role_fp = "/srv/proj/roles/myrole/tasks/main.yml"
        g, tid = _make_task(
            file_path=role_fp,
            yaml_lines="when: 'web1' in groups['webservers']\n",
        )
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        groups = cast(list[str], result.detail.get("found_groups") or [])
        assert "webservers" in groups

    def test_violation_relative_role_path(self, rule: HardcodedGroupGraphRule) -> None:
        """Violation for ``groups['...']`` using a relative role path.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(
            file_path="roles/myrole/tasks/main.yml",
            yaml_lines="when: 'db1' in groups['databases']\n",
        )
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        groups = cast(list[str], result.detail.get("found_groups") or [])
        assert "databases" in groups

    def test_pass_not_under_roles(self, rule: HardcodedGroupGraphRule) -> None:
        """Pass when file path is not under ``/roles/`` (rule does not apply).

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(
            file_path="site.yml",
            yaml_lines="when: 'web1' in groups['webservers']\n",
        )
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L091 — BoolFilter
# ---------------------------------------------------------------------------


class TestL091BoolFilterGraphRule:
    """Tests for L091 BoolFilterGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> BoolFilterGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return BoolFilterGraphRule()

    def test_violation_bare_when_variable(self, rule: BoolFilterGraphRule) -> None:
        """Violation for bare variable in ``when`` without ``| bool``.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="when: use_feature\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        bare_when = cast(list[str], result.detail.get("bare_variables") or [])
        assert "use_feature" in bare_when

    def test_pass_when_with_bool_filter(self, rule: BoolFilterGraphRule) -> None:
        """Pass when ``when`` uses ``| bool`` on the variable.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="when: use_feature | bool\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L094 — DynamicTemplateDate
# ---------------------------------------------------------------------------


class TestL094DynamicTemplateDateGraphRule:
    """Tests for L094 DynamicTemplateDateGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> DynamicTemplateDateGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return DynamicTemplateDateGraphRule()

    def test_violation_dynamic_date_in_template_task(self, rule: DynamicTemplateDateGraphRule) -> None:
        """Violation when template task content references dynamic date patterns.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(
            module="ansible.builtin.template",
            yaml_lines="src: report.j2\ndest: /tmp/out\nwhen: ansible_date_time is defined\n",
        )
        assert rule.match(g, tid) is True
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None

    def test_violation_unresolved_template_module(self, rule: DynamicTemplateDateGraphRule) -> None:
        """Violation when resolved name is empty but declared module is ``template``.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(
            module="template",
            yaml_lines="src: report.j2\ndest: /tmp/out\nwhen: ansible_date_time is defined\n",
        )
        assert rule.match(g, tid) is True
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_pass_template_without_dynamic_date(self, rule: DynamicTemplateDateGraphRule) -> None:
        """Pass for template tasks without dynamic date expressions.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(
            module="ansible.builtin.template",
            yaml_lines="src: static.j2\n  dest: /tmp/out\n",
        )
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# M014 — TopLevelFactVariables
# ---------------------------------------------------------------------------


class TestM014TopLevelFactVariablesGraphRule:
    """Tests for M014 TopLevelFactVariablesGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> TopLevelFactVariablesGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return TopLevelFactVariablesGraphRule()

    def test_violation_deprecated_ansible_fact_var(self, rule: TopLevelFactVariablesGraphRule) -> None:
        """Violation for ``ansible_*`` fact names removed in 2.24 (not magic vars).

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="msg: {{ ansible_eth0 }}\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        m014_facts = cast(list[str], result.detail.get("found_facts") or [])
        assert "ansible_eth0" in m014_facts

    def test_pass_magic_ansible_var_allowed(self, rule: TopLevelFactVariablesGraphRule) -> None:
        """Pass when only allowed magic ``ansible_*`` variables appear.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="msg: {{ ansible_play_name }}\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# M015 — PlayHostsMagicVariable
# ---------------------------------------------------------------------------


class TestM015PlayHostsMagicVariableGraphRule:
    """Tests for M015 PlayHostsMagicVariableGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> PlayHostsMagicVariableGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return PlayHostsMagicVariableGraphRule()

    def test_violation_play_hosts_reference(self, rule: PlayHostsMagicVariableGraphRule) -> None:
        """Violation when deprecated ``play_hosts`` is referenced.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="when: host in play_hosts\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail.get("replacement") == "ansible_play_batch"

    def test_pass_no_play_hosts(self, rule: PlayHostsMagicVariableGraphRule) -> None:
        """Pass when ``play_hosts`` is not used.

        Args:
            rule: Rule instance under test.

        Returns:
            None
        """
        g, tid = _make_task(yaml_lines="when: inventory_hostname in groups['all']\n")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# Scanner integration
# ---------------------------------------------------------------------------


class TestPhase2HYamlLinesScannerIntegration:
    """Scanner integration tests for Phase 2H yaml_lines graph rules."""

    def test_scan_l040_tabs(self) -> None:
        """Scanner records L040 when raw YAML contains tabs.

        Returns:
            None
        """
        g, _tid = _make_task(yaml_lines="name: x\n\tbad\n")
        rules: list[GraphRule] = [NoTabsGraphRule()]
        report = scan(g, rules)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert len(violations) == 1
        assert violations[0].rule is not None
        assert violations[0].rule.rule_id == "L040"

    def test_scan_l041_and_l098_on_same_task(self) -> None:
        """Scanner runs multiple Phase 2H rules on one task node.

        Duplicate ``when`` triggers L098; ``name`` after ``debug`` triggers L041.

        Returns:
            None
        """
        yaml_text = "- debug:\n    msg: x\n  when: true\n  when: true\n  name: late\n"
        g, _tid = _make_task(module="debug", yaml_lines=yaml_text)
        rules: list[GraphRule] = [KeyOrderGraphRule(), YamlKeyDuplicatesGraphRule()]
        report = scan(g, rules)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        ids = {rr.rule.rule_id for rr in violations if rr.rule is not None}
        assert "L041" in ids
        assert "L098" in ids

    def test_scan_clean_task_no_violations(self) -> None:
        """Scanner produces no violations for conforming task YAML.

        Returns:
            None
        """
        yaml_text = "- name: OK\n  debug:\n    msg: \"{{ foo | default('') }}\"\n"
        g, _tid = _make_task(module="debug", yaml_lines=yaml_text)
        rules: list[GraphRule] = [
            NoTabsGraphRule(),
            KeyOrderGraphRule(),
            DeprecatedBareVarsGraphRule(),
            JinjaGraphRule(),
        ]
        report = scan(g, rules)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert violations == []
