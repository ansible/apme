# L004: Deprecated module (uses short_module_name from _helpers.rego)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := deprecated_module(tree, node)
}

deprecated_module(tree, node) := v if {
	node.type == "taskcall"
	node.module != ""
	short := short_module_name(node.module)
	data.apme.ansible.deprecated_modules[_] == short
	count(node.line) > 0
	v := {
		"rule_id": "L004",
		"level": "error",
		"message": sprintf("Deprecated module: %s", [short]),
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
