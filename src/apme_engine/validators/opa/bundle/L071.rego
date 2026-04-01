# L071: Consider using template instead of copy with Jinja content

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := use_template_over_copy(tree, node)
}

_copy_modules := {"ansible.builtin.copy", "ansible.legacy.copy", "copy"}

use_template_over_copy(tree, node) := v if {
	node.type == "taskcall"
	_copy_modules[node.module]
	mo := object.get(node, "module_options", {})
	content := object.get(mo, "content", null)
	content != null
	contains(content, "{{")
	count(node.line) > 0
	v := {
		"rule_id": "L071",
		"severity": "info",
		"message": "Consider using template instead of copy with Jinja content",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
