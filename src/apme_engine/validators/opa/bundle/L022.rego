# L022: Shell with pipe should set set -o pipefail

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := risky_shell_pipe(tree, node)
}

_has_pipefail(cmd, executable) if {
	contains(cmd, "pipefail")
}

_has_pipefail(cmd, executable) if {
	contains(executable, "pipefail")
}

risky_shell_pipe(tree, node) := v if {
	node.type == "taskcall"
	{"ansible.builtin.shell", "ansible.legacy.shell", "shell"}[node.module]
	mo := object.get(node, "module_options", {})
	cmd := mo["cmd"]
	is_string(cmd)
	contains(cmd, "|")
	executable := object.get(mo, "executable", "")
	not _has_pipefail(cmd, executable)
	count(node.line) > 0
	v := {
		"rule_id": "L022",
		"level": "warning",
		"message": "Shell with pipe should set set -o pipefail or use executable with pipefail",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
