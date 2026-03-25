# Tests for M016: Empty when conditional

package apme.rules_test

import data.apme.rules

test_M016_fires_on_empty_string if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": ""}, "line": [1], "key": "k", "file": "f.yml", "module": "debug"}]}
	node := tree.nodes[0]
	v := rules.empty_when_conditional(tree, node)
	v.rule_id == "M016"
}

test_M016_fires_on_null if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": null}, "line": [1], "key": "k", "file": "f.yml", "module": "debug"}]}
	node := tree.nodes[0]
	v := rules.empty_when_conditional(tree, node)
	v.rule_id == "M016"
}

test_M016_no_fire_on_real_condition if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "foo is defined"}, "line": [1], "key": "k", "file": "f.yml", "module": "debug"}]}
	node := tree.nodes[0]
	not rules.empty_when_conditional(tree, node)
}

test_M016_no_fire_without_when if {
	tree := {"nodes": [{"type": "taskcall", "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "debug"}]}
	node := tree.nodes[0]
	not rules.empty_when_conditional(tree, node)
}
