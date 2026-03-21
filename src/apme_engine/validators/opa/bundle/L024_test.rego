# Integration tests for L024: Task should have a name

package apme.rules_test

import data.apme.rules

test_L024_fires_when_task_has_no_name if {
	tree := {"nodes": [{"type": "taskcall", "name": null, "line": [2], "key": "k", "file": "tasks/main.yml"}]}
	node := tree.nodes[0]
	v := rules.name_missing(tree, node)
	v.rule_id == "L024"
	v.level == "low"
}

test_L024_does_not_fire_when_task_has_name if {
	tree := {"nodes": [{"type": "taskcall", "name": "Install package", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.name_missing(tree, node)
}
