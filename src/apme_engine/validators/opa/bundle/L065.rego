# L065: No Jinja in play names

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := no_jinja_play_name(tree, node)
}

no_jinja_play_name(tree, node) := v if {
	node.type == "playcall"
	name := object.get(node, "name", "")
	name != null
	contains(name, "{{")
	count(node.line) > 0
	v := {
		"rule_id": "L065",
		"severity": "low",
		"message": "Play names should not contain Jinja expressions",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "play",
	}
}

no_jinja_play_name(tree, node) := v if {
	node.type == "playcall"
	name := object.get(node, "name", "")
	name != null
	contains(name, "{%")
	count(node.line) > 0
	v := {
		"rule_id": "L065",
		"severity": "low",
		"message": "Play names should not contain Jinja expressions",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "play",
	}
}
