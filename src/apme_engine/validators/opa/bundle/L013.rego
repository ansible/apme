# L013: command/shell/raw should have changed_when or creates/removes (uses cmd_shell_modules from _helpers.rego)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := no_changed_when(tree, node)
}

no_changed_when(tree, node) := v if {
	node.type == "taskcall"
	cmd_shell_modules[node.module]
	opts := object.get(node, "options", {})
	mo := object.get(node, "module_options", {})
	object.get(opts, "changed_when", null) == null
	object.get(mo, "creates", null) == null
	object.get(mo, "removes", null) == null
	count(node.line) > 0
	v := {
		"rule_id": "L013",
		"severity": "medium",
		"message": "command/shell/raw should have changed_when or creates/removes",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
