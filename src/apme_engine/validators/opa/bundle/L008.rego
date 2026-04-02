# L008: Do not use local_action; use delegate_to: localhost

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := deprecated_local_action(tree, node)
}

deprecated_local_action(tree, node) := v if {
	node.type == "taskcall"
	object.get(node, "options", {})["local_action"] != null
	count(node.line) > 0
	v := {
		"rule_id": "L008",
		"severity": "low",
		"message": "Do not use local_action; use delegate_to: localhost",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
