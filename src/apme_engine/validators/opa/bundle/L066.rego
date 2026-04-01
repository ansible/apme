# L066: Do not mix roles: and tasks: in the same play

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := mixed_roles_tasks(tree, node)
}

mixed_roles_tasks(tree, node) := v if {
	node.type == "playcall"
	opts := object.get(node, "options", {})
	object.get(opts, "roles", null) != null
	object.get(opts, "tasks", null) != null
	count(node.line) > 0
	v := {
		"rule_id": "L066",
		"severity": "low",
		"message": "Do not mix roles: and tasks: in the same play; use one or the other",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "play",
	}
}
