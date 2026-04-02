# M006: become with ignore_errors may miss timeout (unreachable in 2.19+)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := become_timeout_risk(tree, node)
}

become_timeout_risk(tree, node) := v if {
	node.type == "taskcall"
	opts := object.get(node, "options", {})
	opts["become"] == true
	opts["ignore_errors"] == true
	object.get(opts, "ignore_unreachable", null) == null
	count(node.line) > 0
	v := {
		"rule_id": "M006",
		"severity": "high",
		"message": "become with ignore_errors will not catch timeout in 2.19+; add ignore_unreachable: true or handle differently",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
