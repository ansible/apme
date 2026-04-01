# L009: Avoid comparison to empty string in when

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := empty_string_compare(tree, node)
}

_empty_string_patterns := [" == \"\"", " != \"\"", " == ''", " != ''"]

empty_string_compare(tree, node) := v if {
	node.type == "taskcall"
	when_val := object.get(node, "options", {})["when"]
	is_string(when_val)
	when_val != ""
	some pat in _empty_string_patterns
	contains(when_val, pat)
	count(node.line) > 0
	v := {
		"rule_id": "L009",
		"severity": "medium",
		"message": "Avoid comparison to empty string in when; use truthiness test instead",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
