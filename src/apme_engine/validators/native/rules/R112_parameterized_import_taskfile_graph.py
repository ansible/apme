"""GraphRule R112: detect Jinja-templated path in include/import_tasks."""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult, is_templated

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_TASKFILE_MODULES = frozenset(
    {
        "ansible.builtin.include_tasks",
        "ansible.builtin.import_tasks",
        "ansible.legacy.include_tasks",
        "ansible.legacy.import_tasks",
        "include_tasks",
        "import_tasks",
    }
)


@dataclass
class ParameterizedImportTaskfileGraphRule(GraphRule):
    """Detect include/import_tasks with a Jinja-templated file path.

    A parameterized taskfile path means the included file is determined
    at runtime, making static analysis incomplete and introducing risk
    if the variable is externally controlled.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "R112"
    description: str = "Import/include a parameterized name of taskfile"
    enabled: bool = True
    name: str = "ParameterizedImportTaskfile"
    version: str = "v0.0.1"
    severity: str = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes that invoke include_tasks or import_tasks.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node invokes a taskfile-type module.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        mod = node.module
        return mod in _TASKFILE_MODULES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report when the taskfile path contains Jinja2 templates.

        Checks both the ``file`` parameter and ``_raw_params`` (for
        single-line ``include_tasks: {{ var }}.yml`` syntax).

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result, or None if node missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}

        taskfile_ref = mo.get("file", "")
        if not taskfile_ref:
            taskfile_ref = mo.get("_raw_params", "") or mo.get("_raw", "")
        if not isinstance(taskfile_ref, str):
            taskfile_ref = str(taskfile_ref) if taskfile_ref else ""

        if not is_templated(taskfile_ref):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {"taskfile": taskfile_ref}
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
