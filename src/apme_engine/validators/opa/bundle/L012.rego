# L012: Avoid state=latest; pin package versions (uses package_modules from _helpers.rego)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := latest(tree, node)
}

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := package_latest(tree, node)
}

latest(tree, node) := v if {
	node.type == "taskcall"
	package_modules[node.module]
	object.get(node, "module_options", {})["state"] == "latest"
	count(node.line) > 0
	v := {
		"rule_id": "L012",
		"level": "warning",
		"message": "Avoid state=latest; pin package versions",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}

package_latest(tree, node) := latest(tree, node)
