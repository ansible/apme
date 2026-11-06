# L007: Prefer ansible.builtin.command when no shell features needed

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := command_instead_of_shell(tree, node)
}

_shell_modules := {"shell", "ansible.builtin.shell", "ansible.legacy.shell"}

has_shell_executable(node) if {
	module_options := object.get(node, "module_options", {})
	is_object(module_options)
	object.get(module_options, "executable", null) != null
}

has_shell_executable(node) if {
	options := object.get(node, "options", {})
	is_object(options)
	args := object.get(options, "args", {})
	is_object(args)
	object.get(args, "executable", null) != null
}

command_instead_of_shell(tree, node) := v if {
	node.type == "taskcall"
	node.module in _shell_modules
	not has_shell_executable(node)
	cmd := inspectable_command(object.get(node, "module_options", {}))
	cmd != ""
	not uses_shell_features(cmd)
	count(node.line) > 0
	v := {
		"rule_id": "L007",
		"severity": "low",
		"message": "Prefer ansible.builtin.command when no shell features are needed",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
