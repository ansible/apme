# L072: Set backup: true on template/copy tasks

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := missing_backup(tree, node)
}

missing_backup(tree, node) := v if {
	node.type == "taskcall"
	copy_template_modules[node.module]
	mo := object.get(node, "module_options", {})
	object.get(mo, "backup", null) == null
	object.get(mo, "dest", null) != null
	count(node.line) > 0
	v := {
		"rule_id": "L072",
		"severity": "info",
		"message": "Consider setting backup: true on template/copy tasks for safety",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
