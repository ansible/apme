# L024: Task should have a name

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := name_missing(tree, node)
}

name_missing(tree, node) := v if {
	node.type == "taskcall"
	object.get(node, "name", null) == null
	count(node.line) > 0
	v := {
		"rule_id": "L024",
		"level": "warning",
		"message": "Task should have a name",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
