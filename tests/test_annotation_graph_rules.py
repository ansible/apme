"""Unit and integration tests for annotation-based GraphRules (Phase 2I).

Covers R101, R103, R104, R105, R106, R107, R109, R113, R114, R115.
"""

from __future__ import annotations

import pytest

from apme_engine.engine.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.engine.graph_scanner import scan
from apme_engine.engine.models import YAMLDict
from apme_engine.validators.native.rules._module_risk_mapping import (
    RiskProfile,
    get_risk_profile,
    resolve_field,
)
from apme_engine.validators.native.rules.graph_rule_base import GraphRule
from apme_engine.validators.native.rules.R101_command_exec_graph import (
    CommandExecGraphRule,
)
from apme_engine.validators.native.rules.R103_download_exec_graph import (
    DownloadExecGraphRule,
)
from apme_engine.validators.native.rules.R104_unauthorized_download_src_graph import (
    InvalidDownloadSourceGraphRule,
)
from apme_engine.validators.native.rules.R105_outbound_transfer_graph import (
    OutboundTransferGraphRule,
)
from apme_engine.validators.native.rules.R106_inbound_transfer_graph import (
    InboundTransferGraphRule,
)
from apme_engine.validators.native.rules.R107_pkg_install_with_insecure_option_graph import (
    InsecurePkgInstallGraphRule,
)
from apme_engine.validators.native.rules.R109_key_config_change_graph import (
    ConfigChangeGraphRule,
)
from apme_engine.validators.native.rules.R113_parameterized_pkg_install_graph import (
    PkgInstallGraphRule,
)
from apme_engine.validators.native.rules.R114_file_change_graph import (
    FileChangeGraphRule,
)
from apme_engine.validators.native.rules.R115_file_deletion_graph import (
    FileDeletionGraphRule,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    *,
    module: str = "debug",
    resolved_module: str = "",
    module_options: YAMLDict | None = None,
    name: str | None = None,
    file_path: str = "site.yml",
    line_start: int = 10,
    path: str = "site.yml/plays[0]/tasks[0]",
) -> tuple[ContentGraph, str]:
    """Build a minimal playbook -> play -> task graph.

    Args:
        module: Declared module name on the task.
        resolved_module: Resolved FQCN for the module.
        module_options: Module argument mapping.
        name: Optional task name.
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
        resolved_module_name=resolved_module,
        module_options=module_options or {},
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(task)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, task.node_id, EdgeType.CONTAINS)
    return g, task.node_id


def _make_two_tasks(
    *,
    task1_module: str,
    task1_resolved: str = "",
    task1_options: YAMLDict | None = None,
    task1_line: int = 5,
    task2_module: str,
    task2_resolved: str = "",
    task2_options: YAMLDict | None = None,
    task2_line: int = 15,
) -> tuple[ContentGraph, str, str]:
    """Build a graph with two sibling tasks under the same play.

    Args:
        task1_module: Module name for the first task.
        task1_resolved: Resolved FQCN for the first task.
        task1_options: Module options for the first task.
        task1_line: Starting line for the first task.
        task2_module: Module name for the second task.
        task2_resolved: Resolved FQCN for the second task.
        task2_options: Module options for the second task.
        task2_line: Starting line for the second task.

    Returns:
        Tuple of ``(graph, task1_node_id, task2_node_id)``.
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
    t1 = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        line_start=task1_line,
        module=task1_module,
        resolved_module_name=task1_resolved,
        module_options=task1_options or {},
        scope=NodeScope.OWNED,
    )
    t2 = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[1]", node_type=NodeType.TASK),
        file_path="site.yml",
        line_start=task2_line,
        module=task2_module,
        resolved_module_name=task2_resolved,
        module_options=task2_options or {},
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(t1)
    g.add_node(t2)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, t1.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, t2.node_id, EdgeType.CONTAINS)
    return g, t1.node_id, t2.node_id


# ===========================================================================
# _module_risk_mapping unit tests
# ===========================================================================


class TestModuleRiskMapping:
    """Tests for the shared FQCN -> RiskProfile mapping."""

    def test_fqcn_lookup(self) -> None:
        """FQCN ``ansible.builtin.shell`` resolves to cmd_exec."""
        p = get_risk_profile("ansible.builtin.shell")
        assert p is not None
        assert p.risk_type == "cmd_exec"

    def test_short_name_fallback(self) -> None:
        """Short name ``shell`` resolves via alias."""
        p = get_risk_profile("", "shell")
        assert p is not None
        assert p.risk_type == "cmd_exec"

    def test_unknown_module(self) -> None:
        """Unknown module returns None."""
        assert get_risk_profile("unknown.module", "unknown") is None

    def test_resolve_field_direct(self) -> None:
        """Direct field mapping returns the value."""
        profile = RiskProfile(risk_type="test", fields={"src": "url"})
        assert resolve_field({"url": "http://x"}, profile, "src") == "http://x"

    def test_resolve_field_chain(self) -> None:
        """Field chain falls back to second key."""
        profile = RiskProfile(risk_type="test", field_chains={"cmd": ("_raw_params", "cmd")})
        assert resolve_field({"cmd": "ls -la"}, profile, "cmd") == "ls -la"

    def test_resolve_field_missing(self) -> None:
        """Missing field returns None."""
        profile = RiskProfile(risk_type="test", fields={"src": "url"})
        assert resolve_field({}, profile, "src") is None

    def test_uri_method_gate(self) -> None:
        """URI profile has a method gate."""
        p = get_risk_profile("ansible.builtin.uri")
        assert p is not None
        assert p.method_gate is not None
        assert "POST" in p.method_gate


# ===========================================================================
# R101 — CommandExec
# ===========================================================================


class TestR101CommandExecGraphRule:
    """Tests for R101 CommandExecGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> CommandExecGraphRule:
        """Create a rule instance.

        Returns:
            A CommandExecGraphRule.
        """
        return CommandExecGraphRule()

    def test_templated_raw_params(self, rule: CommandExecGraphRule) -> None:
        """Templated ``_raw_params`` triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="shell",
            resolved_module="ansible.builtin.shell",
            module_options={"_raw_params": "{{ my_cmd }}"},
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["cmd"] == "{{ my_cmd }}"

    def test_static_command(self, rule: CommandExecGraphRule) -> None:
        """Static command does not trigger.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="command",
            resolved_module="ansible.builtin.command",
            module_options={"_raw_params": "ls -la"},
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_cmd_key_fallback(self, rule: CommandExecGraphRule) -> None:
        """Falls back to ``cmd`` key when ``_raw_params`` absent.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="command",
            resolved_module="ansible.builtin.command",
            module_options={"cmd": "echo {{ secret }}"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["cmd"] == "echo {{ secret }}"

    def test_no_match_debug(self, rule: CommandExecGraphRule) -> None:
        """Debug module does not match.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(module="debug", resolved_module="ansible.builtin.debug")
        assert not rule.match(g, nid)

    def test_short_name(self, rule: CommandExecGraphRule) -> None:
        """Short module name ``shell`` resolves correctly.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="shell",
            module_options={"_raw_params": "{{ cmd }}"},
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_expect_command_key(self, rule: CommandExecGraphRule) -> None:
        """Expect module uses ``command`` key in its chain.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="expect",
            resolved_module="ansible.builtin.expect",
            module_options={"command": "{{ interactive_cmd }}"},
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True


# ===========================================================================
# R104 — InvalidDownloadSource
# ===========================================================================


class TestR104InvalidDownloadSourceGraphRule:
    """Tests for R104 InvalidDownloadSourceGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> InvalidDownloadSourceGraphRule:
        """Create a rule instance.

        Returns:
            An InvalidDownloadSourceGraphRule.
        """
        return InvalidDownloadSourceGraphRule()

    def test_http_denied(self, rule: InvalidDownloadSourceGraphRule) -> None:
        """HTTP URL is denied by default.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="get_url",
            resolved_module="ansible.builtin.get_url",
            module_options={"url": "http://evil.com/pkg.tar.gz", "dest": "/tmp/"},
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["invalid_src"] == "http://evil.com/pkg.tar.gz"

    def test_https_allowed(self, rule: InvalidDownloadSourceGraphRule) -> None:
        """HTTPS URL is allowed by default.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="get_url",
            resolved_module="ansible.builtin.get_url",
            module_options={"url": "https://safe.com/pkg.tar.gz", "dest": "/tmp/"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_git_http_denied(self, rule: InvalidDownloadSourceGraphRule) -> None:
        """HTTP git repo is denied.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="git",
            resolved_module="ansible.builtin.git",
            module_options={"repo": "http://git.evil.com/repo.git", "dest": "/opt/repo"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_no_match_file(self, rule: InvalidDownloadSourceGraphRule) -> None:
        """File module does not match (not inbound).

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="file",
            resolved_module="ansible.builtin.file",
            module_options={"path": "/tmp/x", "state": "directory"},
        )
        assert not rule.match(g, nid)


# ===========================================================================
# R107 — InsecurePkgInstall
# ===========================================================================


class TestR107InsecurePkgInstallGraphRule:
    """Tests for R107 InsecurePkgInstallGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> InsecurePkgInstallGraphRule:
        """Create a rule instance.

        Returns:
            An InsecurePkgInstallGraphRule.
        """
        return InsecurePkgInstallGraphRule()

    def test_validate_certs_false(self, rule: InsecurePkgInstallGraphRule) -> None:
        """``validate_certs: false`` triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="yum",
            resolved_module="ansible.builtin.yum",
            module_options={"name": "httpd", "validate_certs": False},
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["pkg"] == "httpd"

    def test_gpg_check_disabled(self, rule: InsecurePkgInstallGraphRule) -> None:
        """``disable_gpg_check: true`` triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="dnf",
            resolved_module="ansible.builtin.dnf",
            module_options={"name": "nginx", "disable_gpg_check": True},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_allow_downgrade(self, rule: InsecurePkgInstallGraphRule) -> None:
        """``allow_downgrade: true`` triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="dnf",
            resolved_module="ansible.builtin.dnf",
            module_options={"name": "pkg", "allow_downgrade": True},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_secure_install(self, rule: InsecurePkgInstallGraphRule) -> None:
        """Normal install without insecure flags passes.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="yum",
            resolved_module="ansible.builtin.yum",
            module_options={"name": "httpd"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_string_false(self, rule: InsecurePkgInstallGraphRule) -> None:
        """String ``'false'`` for validate_certs triggers.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="yum",
            resolved_module="ansible.builtin.yum",
            module_options={"name": "httpd", "validate_certs": "false"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True


# ===========================================================================
# R113 — PkgInstall (parameterized)
# ===========================================================================


class TestR113PkgInstallGraphRule:
    """Tests for R113 PkgInstallGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> PkgInstallGraphRule:
        """Create a rule instance.

        Returns:
            A PkgInstallGraphRule.
        """
        return PkgInstallGraphRule()

    def test_templated_pkg(self, rule: PkgInstallGraphRule) -> None:
        """Templated package name triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="yum",
            resolved_module="ansible.builtin.yum",
            module_options={"name": "{{ pkg_name }}"},
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["pkg"] == "{{ pkg_name }}"

    def test_static_pkg(self, rule: PkgInstallGraphRule) -> None:
        """Static package name does not trigger.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="apt",
            resolved_module="ansible.builtin.apt",
            module_options={"name": "nginx"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_pip_requirements_fallback(self, rule: PkgInstallGraphRule) -> None:
        """Pip falls back to ``requirements`` key.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="pip",
            resolved_module="ansible.builtin.pip",
            module_options={"requirements": "{{ req_file }}"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_apt_deb_fallback(self, rule: PkgInstallGraphRule) -> None:
        """Apt falls back to ``deb`` key.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="apt",
            resolved_module="ansible.builtin.apt",
            module_options={"deb": "{{ deb_url }}"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True


# ===========================================================================
# R105 — OutboundTransfer
# ===========================================================================


class TestR105OutboundTransferGraphRule:
    """Tests for R105 OutboundTransferGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> OutboundTransferGraphRule:
        """Create a rule instance.

        Returns:
            An OutboundTransferGraphRule.
        """
        return OutboundTransferGraphRule()

    def test_post_templated_url(self, rule: OutboundTransferGraphRule) -> None:
        """POST with templated URL triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="uri",
            resolved_module="ansible.builtin.uri",
            module_options={
                "url": "{{ callback_url }}",
                "method": "POST",
                "body": "data",
            },
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["to"] == "{{ callback_url }}"
        assert result.detail["from"] == "data"

    def test_get_not_matched(self, rule: OutboundTransferGraphRule) -> None:
        """GET method does not match the outbound profile.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="uri",
            resolved_module="ansible.builtin.uri",
            module_options={"url": "{{ url }}", "method": "GET"},
        )
        assert not rule.match(g, nid)

    def test_put_static_url(self, rule: OutboundTransferGraphRule) -> None:
        """PUT with static URL does not trigger.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="uri",
            resolved_module="ansible.builtin.uri",
            module_options={"url": "https://api.example.com/data", "method": "PUT"},
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_default_method_get(self, rule: OutboundTransferGraphRule) -> None:
        """No method defaults to GET, which does not match.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="uri",
            resolved_module="ansible.builtin.uri",
            module_options={"url": "{{ url }}"},
        )
        assert not rule.match(g, nid)


# ===========================================================================
# R106 — InboundTransfer
# ===========================================================================


class TestR106InboundTransferGraphRule:
    """Tests for R106 InboundTransferGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> InboundTransferGraphRule:
        """Create a rule instance.

        Returns:
            An InboundTransferGraphRule.
        """
        return InboundTransferGraphRule()

    def test_templated_src(self, rule: InboundTransferGraphRule) -> None:
        """Templated source URL triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="get_url",
            resolved_module="ansible.builtin.get_url",
            module_options={"url": "{{ download_url }}", "dest": "/tmp/pkg"},
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["from"] == "{{ download_url }}"
        assert result.detail["to"] == "/tmp/pkg"

    def test_static_src(self, rule: InboundTransferGraphRule) -> None:
        """Static source does not trigger.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="get_url",
            resolved_module="ansible.builtin.get_url",
            module_options={"url": "https://example.com/file.tar.gz", "dest": "/tmp/"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_git_templated_repo(self, rule: InboundTransferGraphRule) -> None:
        """Templated git repo triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="git",
            resolved_module="ansible.builtin.git",
            module_options={"repo": "{{ repo_url }}", "dest": "/opt/code"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True


# ===========================================================================
# R114 — FileChange
# ===========================================================================


class TestR114FileChangeGraphRule:
    """Tests for R114 FileChangeGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> FileChangeGraphRule:
        """Create a rule instance.

        Returns:
            A FileChangeGraphRule.
        """
        return FileChangeGraphRule()

    def test_templated_path(self, rule: FileChangeGraphRule) -> None:
        """Templated file path triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="file",
            resolved_module="ansible.builtin.file",
            module_options={"path": "{{ target_path }}", "state": "directory"},
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["path"] == "{{ target_path }}"

    def test_templated_src(self, rule: FileChangeGraphRule) -> None:
        """Templated source triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="template",
            resolved_module="ansible.builtin.template",
            module_options={"dest": "/etc/conf", "src": "{{ tmpl }}"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert "src" in result.detail

    def test_both_templated(self, rule: FileChangeGraphRule) -> None:
        """Both path and src templated populates both detail keys.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="copy",
            resolved_module="ansible.builtin.copy",
            module_options={"dest": "{{ dest_path }}", "src": "{{ src_file }}"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert "path" in result.detail
        assert "src" in result.detail

    def test_static_path(self, rule: FileChangeGraphRule) -> None:
        """Static paths do not trigger.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="file",
            resolved_module="ansible.builtin.file",
            module_options={"path": "/etc/app/config", "state": "file"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_lineinfile(self, rule: FileChangeGraphRule) -> None:
        """Lineinfile with templated path triggers.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="lineinfile",
            resolved_module="ansible.builtin.lineinfile",
            module_options={"path": "{{ config_file }}", "line": "key=value"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True


# ===========================================================================
# R115 — FileDeletion
# ===========================================================================


class TestR115FileDeletionGraphRule:
    """Tests for R115 FileDeletionGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> FileDeletionGraphRule:
        """Create a rule instance.

        Returns:
            A FileDeletionGraphRule.
        """
        return FileDeletionGraphRule()

    def test_templated_deletion(self, rule: FileDeletionGraphRule) -> None:
        """``state: absent`` with templated path triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="file",
            resolved_module="ansible.builtin.file",
            module_options={"path": "{{ del_path }}", "state": "absent"},
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["path"] == "{{ del_path }}"

    def test_static_deletion(self, rule: FileDeletionGraphRule) -> None:
        """``state: absent`` with static path does not trigger.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="file",
            resolved_module="ansible.builtin.file",
            module_options={"path": "/tmp/cleanup", "state": "absent"},
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_no_match_without_absent(self, rule: FileDeletionGraphRule) -> None:
        """File module without ``state: absent`` does not match.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="file",
            resolved_module="ansible.builtin.file",
            module_options={"path": "{{ p }}", "state": "directory"},
        )
        assert not rule.match(g, nid)


# ===========================================================================
# R109 — ConfigChange
# ===========================================================================


class TestR109ConfigChangeGraphRule:
    """Tests for R109 ConfigChangeGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> ConfigChangeGraphRule:
        """Create a rule instance.

        Returns:
            A ConfigChangeGraphRule.
        """
        return ConfigChangeGraphRule()

    def test_templated_rpm_key(self, rule: ConfigChangeGraphRule) -> None:
        """Templated rpm_key key triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="rpm_key",
            resolved_module="ansible.builtin.rpm_key",
            module_options={"key": "{{ gpg_key_url }}", "state": "present"},
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["key"] == "{{ gpg_key_url }}"

    def test_static_rpm_key(self, rule: ConfigChangeGraphRule) -> None:
        """Static rpm_key key does not trigger.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="rpm_key",
            resolved_module="ansible.builtin.rpm_key",
            module_options={"key": "https://rpm.example.com/key.gpg", "state": "present"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_apt_key_url_fallback(self, rule: ConfigChangeGraphRule) -> None:
        """apt_key falls back to ``url`` for the key field.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="apt_key",
            resolved_module="ansible.builtin.apt_key",
            module_options={"url": "{{ key_url }}"},
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_apt_key_data_fallback(self, rule: ConfigChangeGraphRule) -> None:
        """apt_key falls back to ``data`` when ``url`` absent.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="apt_key",
            resolved_module="ansible.builtin.apt_key",
            module_options={"data": "{{ key_data }}"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True


# ===========================================================================
# R103 — DownloadExec (cross-task)
# ===========================================================================


class TestR103DownloadExecGraphRule:
    """Tests for R103 DownloadExecGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> DownloadExecGraphRule:
        """Create a rule instance.

        Returns:
            A DownloadExecGraphRule.
        """
        return DownloadExecGraphRule()

    def test_download_then_exec(self, rule: DownloadExecGraphRule) -> None:
        """Preceding get_url with templated src + dest in command triggers.

        Args:
            rule: Rule instance under test.
        """
        g, _, exec_id = _make_two_tasks(
            task1_module="get_url",
            task1_resolved="ansible.builtin.get_url",
            task1_options={"url": "{{ malicious_url }}", "dest": "/tmp/install.sh"},
            task1_line=5,
            task2_module="shell",
            task2_resolved="ansible.builtin.shell",
            task2_options={"_raw_params": "/tmp/install.sh"},
            task2_line=15,
        )
        assert rule.match(g, exec_id)
        result = rule.process(g, exec_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["src"] == "{{ malicious_url }}"
        assert result.detail["executed_file"] == "/tmp/install.sh"

    def test_no_preceding_inbound(self, rule: DownloadExecGraphRule) -> None:
        """Command without prior inbound does not trigger.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="shell",
            resolved_module="ansible.builtin.shell",
            module_options={"_raw_params": "/tmp/install.sh"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_dest_not_in_command(self, rule: DownloadExecGraphRule) -> None:
        """Inbound dest not referenced in command does not trigger.

        Args:
            rule: Rule instance under test.
        """
        g, _, exec_id = _make_two_tasks(
            task1_module="get_url",
            task1_resolved="ansible.builtin.get_url",
            task1_options={"url": "{{ url }}", "dest": "/tmp/downloaded"},
            task1_line=5,
            task2_module="shell",
            task2_resolved="ansible.builtin.shell",
            task2_options={"_raw_params": "/opt/other_script.sh"},
            task2_line=15,
        )
        result = rule.process(g, exec_id)
        assert result is not None
        assert result.verdict is False

    def test_static_src_no_trigger(self, rule: DownloadExecGraphRule) -> None:
        """Inbound with static src does not trigger even if dest matches.

        Args:
            rule: Rule instance under test.
        """
        g, _, exec_id = _make_two_tasks(
            task1_module="get_url",
            task1_resolved="ansible.builtin.get_url",
            task1_options={"url": "https://safe.com/script.sh", "dest": "/tmp/script.sh"},
            task1_line=5,
            task2_module="shell",
            task2_resolved="ansible.builtin.shell",
            task2_options={"_raw_params": "/tmp/script.sh"},
            task2_line=15,
        )
        result = rule.process(g, exec_id)
        assert result is not None
        assert result.verdict is False

    def test_later_inbound_ignored(self, rule: DownloadExecGraphRule) -> None:
        """Inbound task AFTER the command is ignored.

        Args:
            rule: Rule instance under test.
        """
        g, dl_id, exec_id = _make_two_tasks(
            task1_module="shell",
            task1_resolved="ansible.builtin.shell",
            task1_options={"_raw_params": "/tmp/install.sh"},
            task1_line=5,
            task2_module="get_url",
            task2_resolved="ansible.builtin.get_url",
            task2_options={"url": "{{ url }}", "dest": "/tmp/install.sh"},
            task2_line=15,
        )
        assert rule.match(g, dl_id)
        result = rule.process(g, dl_id)
        assert result is not None
        assert result.verdict is False

    def test_block_nested_download_exec(self, rule: DownloadExecGraphRule) -> None:
        """Download and exec inside the same block triggers violation.

        Tasks inside a ``block:`` are CONTAINS children of the BLOCK node,
        not direct children of the PLAY.  R103 must walk descendants, not
        just direct children.

        Args:
            rule: Rule instance under test.
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
        block = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.BLOCK),
            file_path="site.yml",
            line_start=3,
            scope=NodeScope.OWNED,
        )
        dl_task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]/block[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=5,
            module="get_url",
            resolved_module_name="ansible.builtin.get_url",
            module_options={"url": "{{ evil_url }}", "dest": "/tmp/run.sh"},
            scope=NodeScope.OWNED,
        )
        exec_task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]/block[1]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module="shell",
            resolved_module_name="ansible.builtin.shell",
            module_options={"_raw_params": "/tmp/run.sh"},
            scope=NodeScope.OWNED,
        )
        g.add_node(pb)
        g.add_node(play)
        g.add_node(block)
        g.add_node(dl_task)
        g.add_node(exec_task)
        g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, block.node_id, EdgeType.CONTAINS)
        g.add_edge(block.node_id, dl_task.node_id, EdgeType.CONTAINS)
        g.add_edge(block.node_id, exec_task.node_id, EdgeType.CONTAINS)

        assert rule.match(g, exec_task.node_id)
        result = rule.process(g, exec_task.node_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["executed_file"] == "/tmp/run.sh"


# ===========================================================================
# Scanner integration tests
# ===========================================================================


class TestAnnotationRulesScanner:
    """Integration tests running rules through the graph scanner."""

    @staticmethod
    def _violations_from(report: object) -> list[object]:
        """Extract violations from a GraphScanReport.

        Args:
            report: GraphScanReport returned by ``scan()``.

        Returns:
            List of GraphRuleResult instances with ``verdict=True``.
        """
        results = []
        for nr in report.node_results:  # type: ignore[attr-defined]
            for rr in nr.rule_results:
                if rr.verdict:
                    results.append(rr)
        return results

    def _scan_with(
        self,
        rules: list[GraphRule],
        module: str,
        resolved: str,
        options: YAMLDict,
    ) -> list[object]:
        """Run rules through scanner and return violations.

        Args:
            rules: List of GraphRule instances.
            module: Task module name.
            resolved: Resolved FQCN.
            options: Module options dict.

        Returns:
            List of failed GraphRuleResults.
        """
        g, _ = _make_task(
            module=module,
            resolved_module=resolved,
            module_options=options,
        )
        report = scan(g, rules)
        return self._violations_from(report)

    def test_r101_via_scanner(self) -> None:
        """R101 fires through scanner."""
        violations = self._scan_with(
            [CommandExecGraphRule()],
            "shell",
            "ansible.builtin.shell",
            {"_raw_params": "{{ cmd }}"},
        )
        assert len(violations) == 1

    def test_r107_via_scanner(self) -> None:
        """R107 fires through scanner."""
        violations = self._scan_with(
            [InsecurePkgInstallGraphRule()],
            "yum",
            "ansible.builtin.yum",
            {"name": "httpd", "validate_certs": False},
        )
        assert len(violations) == 1

    def test_r113_via_scanner(self) -> None:
        """R113 fires through scanner."""
        violations = self._scan_with(
            [PkgInstallGraphRule()],
            "dnf",
            "ansible.builtin.dnf",
            {"name": "{{ pkg }}"},
        )
        assert len(violations) == 1

    def test_r104_via_scanner(self) -> None:
        """R104 fires through scanner for HTTP URL."""
        violations = self._scan_with(
            [InvalidDownloadSourceGraphRule()],
            "get_url",
            "ansible.builtin.get_url",
            {"url": "http://bad.com/x", "dest": "/tmp/"},
        )
        assert len(violations) == 1

    def test_r106_via_scanner(self) -> None:
        """R106 fires through scanner."""
        violations = self._scan_with(
            [InboundTransferGraphRule()],
            "get_url",
            "ansible.builtin.get_url",
            {"url": "{{ dl_url }}", "dest": "/tmp/"},
        )
        assert len(violations) == 1

    def test_r105_via_scanner(self) -> None:
        """R105 fires through scanner for POST with templated URL."""
        violations = self._scan_with(
            [OutboundTransferGraphRule()],
            "uri",
            "ansible.builtin.uri",
            {"url": "{{ api }}", "method": "POST"},
        )
        assert len(violations) == 1

    def test_r114_via_scanner(self) -> None:
        """R114 fires through scanner."""
        violations = self._scan_with(
            [FileChangeGraphRule()],
            "file",
            "ansible.builtin.file",
            {"path": "{{ p }}", "state": "file"},
        )
        assert len(violations) == 1

    def test_r115_via_scanner(self) -> None:
        """R115 fires through scanner for deletion with templated path."""
        violations = self._scan_with(
            [FileDeletionGraphRule()],
            "file",
            "ansible.builtin.file",
            {"path": "{{ p }}", "state": "absent"},
        )
        assert len(violations) == 1

    def test_r109_via_scanner(self) -> None:
        """R109 fires through scanner."""
        violations = self._scan_with(
            [ConfigChangeGraphRule()],
            "rpm_key",
            "ansible.builtin.rpm_key",
            {"key": "{{ key }}", "state": "present"},
        )
        assert len(violations) == 1

    def test_r103_via_scanner(self) -> None:
        """R103 fires through scanner for download-then-execute."""
        g, _, exec_id = _make_two_tasks(
            task1_module="get_url",
            task1_resolved="ansible.builtin.get_url",
            task1_options={"url": "{{ url }}", "dest": "/tmp/run.sh"},
            task1_line=5,
            task2_module="shell",
            task2_resolved="ansible.builtin.shell",
            task2_options={"_raw_params": "/tmp/run.sh"},
            task2_line=15,
        )
        report = scan(g, [DownloadExecGraphRule()])
        violations = self._violations_from(report)
        assert len(violations) == 1
