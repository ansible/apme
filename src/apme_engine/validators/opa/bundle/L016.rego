# L016: pause without seconds/minutes prompts for input

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := no_prompting(tree, node)
}

no_prompting(tree, node) := v if {
	node.type == "taskcall"
	{"ansible.builtin.pause", "ansible.legacy.pause", "pause"}[node.module]
	mo := object.get(node, "module_options", {})
	object.get(mo, "minutes", null) == null
	object.get(mo, "seconds", null) == null
	count(node.line) > 0
	v := {
		"rule_id": "L016",
		"level": "info",
		"message": "pause without seconds/minutes prompts for input; avoid in CI",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
