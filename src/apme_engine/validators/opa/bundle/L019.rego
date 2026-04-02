# L019: Playbook should have .yml or .yaml extension

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	v := playbook_extension(tree)
}

playbook_extension(tree) := v if {
	tree.root_type == "playbook"
	root_path := object.get(tree, "root_path", "")
	root_path != ""
	endswith(root_path, ".yml") == false
	endswith(root_path, ".yaml") == false
	v := {
		"rule_id": "L019",
		"severity": "low",
		"message": "Playbook should have .yml or .yaml extension",
		"file": root_path,
		"line": 1,
		"path": tree.root_key,
		"scope": "playbook",
	}
}
