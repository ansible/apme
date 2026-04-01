# L068: Avoid lineinfile; prefer template, ini_file, or blockinfile

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := avoid_lineinfile(tree, node)
}

_lineinfile_modules := {"ansible.builtin.lineinfile", "ansible.legacy.lineinfile", "lineinfile"}

avoid_lineinfile(tree, node) := v if {
	node.type == "taskcall"
	_lineinfile_modules[node.module]
	count(node.line) > 0
	v := {
		"rule_id": "L068",
		"severity": "info",
		"message": "Avoid lineinfile; prefer template, ini_file, or blockinfile",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
