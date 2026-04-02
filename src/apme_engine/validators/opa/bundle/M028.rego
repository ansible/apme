# M028: first_found lookup auto-splitting paths on delimiters is deprecated (2.23)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := first_found_auto_split(tree, node)
}

_first_found_modules := {
	"ansible.builtin.first_found",
	"first_found",
	"ansible.legacy.first_found",
}

first_found_auto_split(tree, node) := v if {
	node.type == "taskcall"
	_first_found_modules[node.module]
	mopts := object.get(node, "module_options", {})
	terms := mopts["terms"]
	is_string(terms)
	contains(terms, ",")
	count(node.line) > 0
	v := {
		"rule_id": "M028",
		"severity": "high",
		"message": "first_found auto-splitting paths on delimiters is deprecated in 2.23; use a YAML list",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}

first_found_auto_split(tree, node) := v if {
	node.type == "taskcall"
	_first_found_modules[node.module]
	mopts := object.get(node, "module_options", {})
	terms := mopts["terms"]
	is_string(terms)
	contains(terms, ":")
	not regex.match(`^[a-zA-Z]:\\\\`, terms)
	not regex.match(`^https?://`, terms)
	count(node.line) > 0
	v := {
		"rule_id": "M028",
		"severity": "high",
		"message": "first_found auto-splitting paths on delimiters is deprecated in 2.23; use a YAML list",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
