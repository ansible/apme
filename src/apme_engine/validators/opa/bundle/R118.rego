# R118: Task downloads from an external source (inbound transfer)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := inbound_transfer(tree, node)
}

inbound_transfer(tree, node) := v if {
	node.type == "taskcall"
	some ann in node.annotations
	ann.risk_type == "inbound_transfer"
	count(node.line) > 0
	src := object.get(ann, "src", null)
	src_value := object.get(src, "value", "unknown source")
	v := {
		"rule_id": "R118",
		"severity": "info",
		"message": sprintf("Task performs inbound transfer from %s", [src_value]),
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
