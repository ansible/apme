# L070: Jinja in task name should only appear at the end

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := jinja_name_position(tree, node)
}

jinja_name_position(tree, node) := v if {
	node.type == "taskcall"
	name := object.get(node, "name", "")
	name != null
	name != ""
	idx := indexof(name, "{{")
	idx >= 0
	end_idx := indexof(name, "}}")
	end_idx >= 0
	remaining := substring(name, end_idx + 2, -1)
	trimmed := trim_space(remaining)
	trimmed != ""
	count(node.line) > 0
	v := {
		"rule_id": "L070",
		"severity": "info",
		"message": "Jinja in task names should only appear at the end of the name string",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
