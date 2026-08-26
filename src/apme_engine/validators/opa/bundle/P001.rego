# P001: Banned collection - community.general

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := banned_collection(tree, node)
}

banned_collection(tree, node) := v if {
	node.type == "taskcall"
	node.module != ""
	startswith(node.module, "community.general.")
	count(node.line) > 0
	v := {
		"rule_id": "P001",
		"severity": "high",
		"message": sprintf("Banned collection: %s", [node.module]),
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
