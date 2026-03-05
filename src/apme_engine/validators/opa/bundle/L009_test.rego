# Integration tests for L009: Avoid comparison to empty string in when

package apme.rules_test

import data.apme.rules

test_L009_fires_when_double_quote_eq if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "my_var == \"\""}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.empty_string_compare(tree, node)
	v.rule_id == "L009"
}

test_L009_fires_when_double_quote_neq if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "my_var != \"\""}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.empty_string_compare(tree, node)
	v.rule_id == "L009"
}

test_L009_fires_when_single_quote_eq if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "my_var == ''"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.empty_string_compare(tree, node)
	v.rule_id == "L009"
}

test_L009_fires_when_single_quote_neq if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "my_var != ''"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.empty_string_compare(tree, node)
	v.rule_id == "L009"
}

test_L009_does_not_fire_when_no_when if {
	tree := {"nodes": [{"type": "taskcall", "options": {}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.empty_string_compare(tree, node)
}

test_L009_does_not_fire_when_normal_condition if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "my_var is defined"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.empty_string_compare(tree, node)
}

test_L009_does_not_fire_on_bare_empty_when if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": ""}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.empty_string_compare(tree, node)
}
