# L062: Use YAML-style module arguments, not key=value one-liners

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := yaml_module_args(tree, node)
}

yaml_module_args(tree, node) := v if {
	node.type == "taskcall"
	not cmd_shell_modules[node.module]
	node.module != "ansible.builtin.raw"
	node.module != "ansible.legacy.raw"
	node.module != "raw"
	mo := object.get(node, "module_options", {})
	free_form := object.get(mo, "_raw_params", null)
	free_form != null
	contains(free_form, "=")
	count(node.line) > 0
	v := {
		"rule_id": "L062",
		"severity": "low",
		"message": "Use YAML-style module arguments instead of key=value one-liners",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
