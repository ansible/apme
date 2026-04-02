# M024: include_vars ignore_files must be a list, not a string (2.24)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := include_vars_ignore_files_string(tree, node)
}

_include_vars_modules := {
	"ansible.builtin.include_vars",
	"include_vars",
	"ansible.legacy.include_vars",
}

include_vars_ignore_files_string(tree, node) := v if {
	node.type == "taskcall"
	_include_vars_modules[node.module]
	mopts := object.get(node, "module_options", {})
	ignore_val := mopts["ignore_files"]
	is_string(ignore_val)
	count(node.line) > 0
	v := {
		"rule_id": "M024",
		"severity": "high",
		"message": "include_vars ignore_files must be a list, not a string (2.24); wrap in a YAML list",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
