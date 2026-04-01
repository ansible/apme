# L014: Use notify/handler instead of when: result.changed

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := no_handler(tree, node)
}

_handler_patterns := [
	".changed", "is changed", "is not changed",
	"|changed", "|succeeded", "|failed",
]

no_handler(tree, node) := v if {
	node.type == "taskcall"
	when_val := object.get(node, "options", {})["when"]
	is_string(when_val)
	some pat in _handler_patterns
	contains(when_val, pat)
	count(node.line) > 0
	v := {
		"rule_id": "L014",
		"severity": "low",
		"message": "Use notify/handler instead of when: result.changed",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
