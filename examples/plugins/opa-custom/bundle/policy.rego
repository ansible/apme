# Example org policy: ban module prefixes listed in data.json.
# Package apme.plugin — never apme.rules (that is the closed built-in bundle).

package apme.plugin

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	module := object.get(node, "module", "")
	is_string(module)
	some prefix in data.banned_module_prefixes
	startswith(module, prefix)
	v := {
		"rule_id": "EXT-opacustom-001",
		"severity": "high",
		"message": sprintf("Banned collection module: %s", [module]),
		"file": object.get(node, "file", ""),
		"line": _first_line(node),
		"path": object.get(node, "key", object.get(node, "path", "")),
		"scope": "task",
		"ai_guidance": "Replace community.general modules with ansible.builtin or a certified collection equivalent.",
	}
}

_first_line(node) := line if {
	raw := object.get(node, "line", 0)
	is_number(raw)
	line := raw
}

_first_line(node) := line if {
	raw := object.get(node, "line", [])
	is_array(raw)
	count(raw) > 0
	line := raw[0]
}

_first_line(node) := 0 if {
	not is_number(object.get(node, "line", 0))
	raw := object.get(node, "line", [])
	not is_array(raw)
}
