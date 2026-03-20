"""Native rule P004: validate variables and set annotations."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.models import (
    AnsibleRunContext,
    ArgumentsType,
    Rule,
    RuleResult,
    RuleScope,
    RunTargetType,
    Severity,
    TaskCall,
    VariableType,
    YAMLValue,
)
from apme_engine.engine.models import RuleTag as Tag


def is_loop_var(value: str, task: TaskCall) -> bool:
    """Check if value references a loop variable (item or loop_control.loop_var).

    Args:
        value: Jinja expression string to check.
        task: TaskCall with loop/loop_control info.

    Returns:
        True if value references a loop variable.
    """
    skip_variables: list[str] = []
    loop = getattr(task.spec, "loop", None)
    if loop and isinstance(loop, dict):
        skip_variables.extend(list(loop.keys()))

    _v = value.replace(" ", "")

    for var in skip_variables:
        for _prefix in ["}}", "|", "."]:
            pattern = "{{" + var + _prefix
            if pattern in _v:
                return True
    return False


@dataclass
class VariableValidationRule(Rule):
    """Rule to validate variables and set annotations.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope.
        precedence: Evaluation order.
    """

    rule_id: str = "P004"
    description: str = "Validate variables and set annotations"
    enabled: bool = True
    name: str = "VariableValidation"
    version: str = "v0.0.1"
    severity: str = Severity.NONE
    tags: tuple[str, ...] = (Tag.QUALITY,)
    scope: str = RuleScope.INVENTORY
    precedence: int = 0

    def match(self, ctx: AnsibleRunContext) -> bool:
        """Check if context has a task target.

        Args:
            ctx: AnsibleRunContext to evaluate.

        Returns:
            True if current target is a task.
        """
        if ctx.current is None:
            return False
        return bool(ctx.current.type == RunTargetType.Task)

    def process(self, ctx: AnsibleRunContext) -> RuleResult | None:
        """Validate variables and set annotations on task.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            None (sets annotations on task).
        """
        task = ctx.current
        if task is None or not isinstance(task, TaskCall):
            return None

        undefined_variables: list[str] = []
        unknown_name_vars: list[str] = []
        unnecessary_loop: list[dict[str, str]] = []
        task_arg_keys_list: list[str] = []
        raw_args = task.args.raw
        if task.args.type == ArgumentsType.DICT and isinstance(raw_args, dict):
            task_arg_keys_list = list(raw_args.keys())

        registered_vars: list[str] = []
        for v_name in task.variable_set:
            v = task.variable_set[v_name]
            if isinstance(v, list) and v and getattr(v[-1], "type", None) == VariableType.RegisteredVars:
                registered_vars.append(v_name)

        for v_name in task.variable_use:
            first_v_name = v_name.split(".")[0]
            # skip registered vars
            if first_v_name in registered_vars:
                continue

            v = task.variable_use[v_name]
            if isinstance(v, list) and v and getattr(v[-1], "type", None) == VariableType.Unknown:
                if v_name not in undefined_variables:
                    undefined_variables.append(v_name)
                existing_names = [x["name"] for x in unnecessary_loop if isinstance(x, dict) and "name" in x]
                if v_name not in unknown_name_vars and v_name not in task_arg_keys_list:
                    unknown_name_vars.append(v_name)
                if v_name not in existing_names:
                    v_str = "{{ " + v_name + " }}"
                    if not is_loop_var(v_str, task):
                        unnecessary_loop.append({"name": v_name, "suggested": v_name.replace("item.", "")})

        task.set_annotation("variable.undefined_vars", cast(YAMLValue, undefined_variables), rule_id=self.rule_id)
        task.set_annotation("variable.unknown_name_vars", cast(YAMLValue, unknown_name_vars), rule_id=self.rule_id)
        task.set_annotation("variable.unnecessary_loop_vars", cast(YAMLValue, unnecessary_loop), rule_id=self.rule_id)

        return None
