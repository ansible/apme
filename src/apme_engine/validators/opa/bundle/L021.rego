# L021: Consider setting mode explicitly for file/copy/template (uses file_permission_modules from _helpers.rego)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := risky_file_permissions(tree, node)
}

risky_file_permissions(tree, node) := v if {
	node.type == "taskcall"
	file_permission_modules[node.module]
	object.get(object.get(node, "module_options", {}), "mode", null) == null
	count(node.line) > 0
	v := {
		"rule_id": "L021",
		"level": "warning",
		"message": "Consider setting mode explicitly for file/copy/template tasks",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
