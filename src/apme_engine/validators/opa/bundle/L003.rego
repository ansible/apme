# L003: Play should have a name

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := play_has_no_name(tree, node)
}

play_has_no_name(tree, node) := v if {
	node.type == "playcall"
	object.get(node, "name", null) == null
	count(node.line) > 0
	v := {
		"rule_id": "L003",
		"level": "info",
		"message": "Play should have a name",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "play",
	}
}
