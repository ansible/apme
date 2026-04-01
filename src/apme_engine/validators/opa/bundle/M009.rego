# M009: with_* loops are deprecated; use loop: instead

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := deprecated_with_loop(tree, node)
}

_with_keys := [
	"with_items", "with_dict", "with_fileglob", "with_subelements",
	"with_sequence", "with_nested", "with_first_found",
	"with_indexed_items", "with_flattened", "with_together",
	"with_random_choice", "with_lines", "with_ini",
	"with_inventory_hostnames", "with_cartesian",
]

deprecated_with_loop(tree, node) := v if {
	node.type == "taskcall"
	opts := object.get(node, "options", {})
	some wk in _with_keys
	opts[wk] != null
	count(node.line) > 0
	v := {
		"rule_id": "M009",
		"severity": "high",
		"message": sprintf("with_* loops are deprecated; use loop: instead of %s", [wk]),
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"with_key": wk,
		"scope": "task",
	}
}
