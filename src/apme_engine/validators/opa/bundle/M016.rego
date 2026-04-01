# M016: Empty when: conditional is deprecated (2.23)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := empty_when_conditional(tree, node)
}

empty_when_conditional(tree, node) := v if {
	node.type == "taskcall"
	opts := object.get(node, "options", {})
	when_val := opts["when"]
	when_val == ""
	count(node.line) > 0
	v := {
		"rule_id": "M016",
		"severity": "high",
		"message": "Empty when: conditional is deprecated and will be an error in 2.23; remove the when: key or add an explicit condition",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}

empty_when_conditional(tree, node) := v if {
	node.type == "taskcall"
	opts := object.get(node, "options", {})
	when_val := opts["when"]
	is_null(when_val)
	count(node.line) > 0
	v := {
		"rule_id": "M016",
		"severity": "high",
		"message": "Empty when: conditional is deprecated and will be an error in 2.23; remove the when: key or add an explicit condition",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
