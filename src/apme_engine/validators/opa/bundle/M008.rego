# M008: Bare include: is removed in 2.19+; use include_tasks or import_tasks
#
# The engine resolves bare `include` to the target taskfile key, so
# node.module may no longer be "include" by the time OPA sees it.
# We check original_module (the literal YAML action key) instead.

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := bare_include(tree, node)
}

_bare_include_names := {"include", "ansible.builtin.include", "ansible.legacy.include"}

bare_include(tree, node) := v if {
	node.type == "taskcall"
	om := object.get(node, "original_module", "")
	_bare_include_names[om]
	count(node.line) > 0
	v := {
		"rule_id": "M008",
		"severity": "high",
		"message": "Bare include: is removed in 2.19+; use include_tasks: (dynamic) or import_tasks: (static)",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
