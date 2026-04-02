# L064: Avoid meta: end_play; prefer meta: end_host

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := avoid_end_play(tree, node)
}

avoid_end_play(tree, node) := v if {
	node.type == "taskcall"
	node.module == "ansible.builtin.meta"
	mo := object.get(node, "module_options", {})
	free_form := object.get(mo, "_raw_params", "")
	free_form == "end_play"
	count(node.line) > 0
	v := {
		"rule_id": "L064",
		"severity": "low",
		"message": "Avoid meta: end_play; prefer meta: end_host",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}

avoid_end_play(tree, node) := v if {
	node.type == "taskcall"
	node.module == "ansible.legacy.meta"
	mo := object.get(node, "module_options", {})
	free_form := object.get(mo, "_raw_params", "")
	free_form == "end_play"
	count(node.line) > 0
	v := {
		"rule_id": "L064",
		"severity": "low",
		"message": "Avoid meta: end_play; prefer meta: end_host",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}

avoid_end_play(tree, node) := v if {
	node.type == "taskcall"
	node.module == "meta"
	mo := object.get(node, "module_options", {})
	free_form := object.get(mo, "_raw_params", "")
	free_form == "end_play"
	count(node.line) > 0
	v := {
		"rule_id": "L064",
		"severity": "low",
		"message": "Avoid meta: end_play; prefer meta: end_host",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
