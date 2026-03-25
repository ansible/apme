# M017: action: as a mapping is deprecated (2.23)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := action_as_mapping(tree, node)
}

action_as_mapping(tree, node) := v if {
	node.type == "taskcall"
	opts := object.get(node, "options", {})
	action_val := opts["action"]
	is_object(action_val)
	count(node.line) > 0
	v := {
		"rule_id": "M017",
		"level": "warning",
		"message": "action: with a mapping value is deprecated in 2.23; use the module key directly with args",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
