# L015: Avoid Jinja in when; use variables

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := no_jinja_when(tree, node)
}

no_jinja_when(tree, node) := v if {
	node.type == "taskcall"
	when_val := object.get(node, "options", {})["when"]
	contains(when_val, "{{")
	count(node.line) > 0
	v := {
		"rule_id": "L015",
		"level": "warning",
		"message": "Avoid Jinja in when; use variables",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}

no_jinja_when(tree, node) := v if {
	node.type == "taskcall"
	when_val := object.get(node, "options", {})["when"]
	contains(when_val, "{%")
	count(node.line) > 0
	v := {
		"rule_id": "L015",
		"level": "warning",
		"message": "Avoid Jinja in when; use variables",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
