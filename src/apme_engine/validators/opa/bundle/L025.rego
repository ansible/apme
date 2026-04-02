# L025: Task/play name should start with uppercase letter

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := name_casing(tree, node)
}

name_casing(tree, node) := v if {
	node.type == "taskcall"
	node.name != ""
	first := substring(node.name, 0, 1)
	lower(first) == first
	count(node.line) > 0
	v := {
		"rule_id": "L025",
		"severity": "low",
		"message": "Task name should start with an uppercase letter",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}

name_casing(tree, node) := v if {
	node.type == "playcall"
	node.name != ""
	first := substring(node.name, 0, 1)
	lower(first) == first
	count(node.line) > 0
	v := {
		"rule_id": "L025",
		"severity": "low",
		"message": "Play name should start with an uppercase letter",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
