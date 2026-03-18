# M008: Bare include: is removed in 2.19+; use include_tasks or import_tasks

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := bare_include(tree, node)
}

bare_include(tree, node) := v if {
	node.type == "taskcall"
	{"include", "ansible.builtin.include", "ansible.legacy.include"}[node.module]
	count(node.line) > 0
	v := {
		"rule_id": "M008",
		"level": "error",
		"message": "Bare include: is removed in 2.19+; use include_tasks: (dynamic) or import_tasks: (static)",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
	}
}
