# L023: Consider whether run_once is appropriate

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := run_once(tree, node)
}

run_once(tree, node) := v if {
	node.type == "taskcall"
	opts := object.get(node, "options", {})
	opts["run_once"] != null
	opts["run_once"] != false
	count(node.line) > 0
	v := {
		"rule_id": "L023",
		"severity": "info",
		"message": "Consider whether run_once is appropriate",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "play",
	}
}
