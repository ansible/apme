# L067: Set verbosity on debug tasks

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := debug_verbosity(tree, node)
}

_debug_modules := {"ansible.builtin.debug", "ansible.legacy.debug", "debug"}

debug_verbosity(tree, node) := v if {
	node.type == "taskcall"
	_debug_modules[node.module]
	mo := object.get(node, "module_options", {})
	object.get(mo, "verbosity", null) == null
	count(node.line) > 0
	v := {
		"rule_id": "L067",
		"severity": "info",
		"message": "Set verbosity on debug tasks to avoid noisy output in production",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
