# L018: become_user should have a corresponding become

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := partial_become_task(tree, node)
}

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := partial_become_play(tree, node)
}

partial_become_task(tree, node) := v if {
	node.type == "taskcall"
	opts := object.get(node, "options", {})
	opts["become_user"] != null
	object.get(opts, "become", null) == null
	count(node.line) > 0
	v := {
		"rule_id": "L018",
		"severity": "high",
		"message": "become_user should have a corresponding become",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "play",
	}
}

partial_become_play(tree, node) := v if {
	node.type == "playcall"
	opts := object.get(node, "options", {})
	opts["become_user"] != null
	object.get(opts, "become", null) == null
	count(node.line) > 0
	v := {
		"rule_id": "L018",
		"severity": "high",
		"message": "become_user should have a corresponding become",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "play",
	}
}
