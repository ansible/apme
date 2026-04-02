# L006: Command used in place of preferred module (uses cmd_shell_modules from _helpers.rego)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := command_instead_of_module(tree, node)
}

command_instead_of_module(tree, node) := v if {
	node.type == "taskcall"
	cmd_shell_modules[node.module]
	mo := object.get(node, "module_options", {})
	cmd := mo["cmd"]
	cmd != null
	trim(cmd, " ") != ""
	first_token := split(trim(cmd, " "), " ")[0]
	suggested := data.apme.ansible.command_to_module[first_token]
	suggested != ""
	count(node.line) > 0
	v := {
		"rule_id": "L006",
		"severity": "low",
		"message": sprintf("%s used in place of %s module", [first_token, suggested]),
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
