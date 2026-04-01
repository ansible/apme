# L069: Batch package names in a list instead of looping with item

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := package_loop(tree, node)
}

package_loop(tree, node) := v if {
	node.type == "taskcall"
	package_modules[node.module]
	opts := object.get(node, "options", {})
	loop_val := object.get(opts, "loop", null)
	loop_val != null
	mo := object.get(node, "module_options", {})
	name_val := object.get(mo, "name", "")
	contains(name_val, "item")
	count(node.line) > 0
	v := {
		"rule_id": "L069",
		"severity": "info",
		"message": "Batch package names in a list instead of looping with item",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}

package_loop(tree, node) := v if {
	node.type == "taskcall"
	package_modules[node.module]
	opts := object.get(node, "options", {})
	with_items := object.get(opts, "with_items", null)
	with_items != null
	mo := object.get(node, "module_options", {})
	name_val := object.get(mo, "name", "")
	contains(name_val, "item")
	count(node.line) > 0
	v := {
		"rule_id": "L069",
		"severity": "info",
		"message": "Batch package names in a list instead of looping with with_items",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
