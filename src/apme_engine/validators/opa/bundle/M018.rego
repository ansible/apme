# M018: paramiko_ssh connection plugin is deprecated (removed in 2.21)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := paramiko_ssh_connection(tree, node)
}

paramiko_ssh_connection(tree, node) := v if {
	node.type == "playcall"
	opts := object.get(node, "options", {})
	conn := object.get(opts, "connection", "")
	conn == "paramiko_ssh"
	count(node.line) > 0
	v := {
		"rule_id": "M018",
		"level": "error",
		"message": "paramiko_ssh connection plugin is removed in 2.21; use connection: ssh",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "play",
	}
}

paramiko_ssh_connection(tree, node) := v if {
	node.type == "taskcall"
	opts := object.get(node, "options", {})
	vars_block := object.get(opts, "vars", {})
	conn := object.get(vars_block, "ansible_connection", "")
	conn == "paramiko_ssh"
	count(node.line) > 0
	v := {
		"rule_id": "M018",
		"level": "error",
		"message": "paramiko_ssh connection plugin is removed in 2.21; use ansible_connection: ssh",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
