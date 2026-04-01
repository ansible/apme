# L007: Prefer ansible.builtin.command when no shell features needed

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := command_instead_of_shell(tree, node)
}

_shell_chars := ["|", "&&", "||", ";", ">", ">>", "<", "$(", "`", "*", "?"]

_uses_shell_features(cmd) if {
	some ch in _shell_chars
	contains(cmd, ch)
}

_shell_modules := {"shell", "ansible.builtin.shell", "ansible.legacy.shell"}

command_instead_of_shell(tree, node) := v if {
	node.type == "taskcall"
	node.module in _shell_modules
	mo := object.get(node, "module_options", {})
	not mo["cmd"]
	count(node.line) > 0
	v := {
		"rule_id": "L007",
		"level": "warning",
		"message": "Prefer ansible.builtin.command when no shell features are needed",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}

command_instead_of_shell(tree, node) := v if {
	node.type == "taskcall"
	node.module in _shell_modules
	cmd := object.get(node, "module_options", {})["cmd"]
	not _uses_shell_features(cmd)
	count(node.line) > 0
	v := {
		"rule_id": "L007",
		"level": "warning",
		"message": "Prefer ansible.builtin.command when no shell features are needed",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
