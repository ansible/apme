# L010: Use failed_when or register instead of ignore_errors

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := ignore_errors(tree, node)
}

ignore_errors(tree, node) := v if {
	node.type == "taskcall"
	opts := object.get(node, "options", {})
	opts["ignore_errors"] != null
	opts["ignore_errors"] != false
	object.get(opts, "register", null) == null
	opts["ignore_errors"] != "{{ ansible_check_mode }}"
	count(node.line) > 0
	v := {
		"rule_id": "L010",
		"severity": "medium",
		"message": "Use failed_when or register instead of ignore_errors",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
