# L063: Block should have a name

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := block_has_no_name(tree, node)
}

block_has_no_name(tree, node) := v if {
	node.type == "blockcall"
	object.get(node, "name", null) == null
	count(node.line) > 0
	v := {
		"rule_id": "L063",
		"severity": "low",
		"message": "Block should have a name",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "block",
	}
}
