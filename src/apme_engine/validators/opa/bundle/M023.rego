# M023: follow_redirects: yes/no string deprecated in url lookup (2.22)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := follow_redirects_string(tree, node)
}

_string_bools := {"yes", "no", "Yes", "No", "YES", "NO"}

follow_redirects_string(tree, node) := v if {
	node.type == "taskcall"
	mopts := object.get(node, "module_options", {})
	fr_val := mopts["follow_redirects"]
	is_string(fr_val)
	_string_bools[fr_val]
	count(node.line) > 0
	v := {
		"rule_id": "M023",
		"severity": "high",
		"message": sprintf("follow_redirects: '%s' (string) is deprecated in 2.22; use true/false boolean", [fr_val]),
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
