# M021: Empty args: keyword on a task is deprecated (2.23)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := empty_args_keyword(tree, node)
}

empty_args_keyword(tree, node) := v if {
	node.type == "taskcall"
	opts := object.get(node, "options", {})
	args_val := opts["args"]
	is_null(args_val)
	count(node.line) > 0
	v := {
		"rule_id": "M021",
		"severity": "high",
		"message": "Empty args: keyword is deprecated in 2.23; remove the args: key",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}

empty_args_keyword(tree, node) := v if {
	node.type == "taskcall"
	opts := object.get(node, "options", {})
	args_val := opts["args"]
	args_val == {}
	count(node.line) > 0
	v := {
		"rule_id": "M021",
		"severity": "high",
		"message": "Empty args: keyword is deprecated in 2.23; remove the args: key",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
