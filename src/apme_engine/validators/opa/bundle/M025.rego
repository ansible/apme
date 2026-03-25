# M025: Third-party strategy plugins are deprecated (2.23)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := third_party_strategy(tree, node)
}

_builtin_strategies := {"linear", "free", "debug", "host_pinned"}

third_party_strategy(tree, node) := v if {
	node.type == "playcall"
	opts := object.get(node, "options", {})
	strategy := opts["strategy"]
	is_string(strategy)
	not _builtin_strategies[strategy]
	not startswith(strategy, "ansible.builtin.")
	count(node.line) > 0
	v := {
		"rule_id": "M025",
		"level": "warning",
		"message": sprintf("Third-party strategy plugin '%s' is deprecated in 2.23; use an ansible.builtin strategy", [strategy]),
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "play",
	}
}
