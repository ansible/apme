# L020: mode should be string with leading zero (uses file_permission_modules, is_number from _helpers.rego)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := risky_octal(tree, node)
}

risky_octal(tree, node) := v if {
	node.type == "taskcall"
	file_permission_modules[node.module]
	mode := object.get(node, "module_options", {})["mode"]
	is_number(mode)
	count(node.line) > 0
	v := {
		"rule_id": "L020",
		"level": "error",
		"message": "mode should be string with leading zero (e.g. \"0644\")",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
