# L011: Avoid comparison to literal true/false in when

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := literal_compare(tree, node)
}

_literal_patterns := [
	" == true", " == false", " != true", " != false",
	" == True", " == False", " != True", " != False",
	" is true", " is false", " is not true", " is not false",
]

literal_compare(tree, node) := v if {
	node.type == "taskcall"
	when_val := object.get(node, "options", {})["when"]
	is_string(when_val)
	some pat in _literal_patterns
	contains(when_val, pat)
	count(node.line) > 0
	v := {
		"rule_id": "L011",
		"level": "warning",
		"message": "Avoid comparison to literal true/false in when; use truthiness test instead",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
	}
}
