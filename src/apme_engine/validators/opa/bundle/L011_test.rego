# Integration tests for L011: Avoid literal true/false in when

package apme.rules_test

import data.apme.rules

test_L011_fires_when_equals_true if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "x == true"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.literal_compare(tree, node)
	v.rule_id == "L011"
}

test_L011_fires_when_equals_false if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "x == false"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.literal_compare(tree, node)
	v.rule_id == "L011"
}

test_L011_fires_when_not_equals_true if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "x != true"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.literal_compare(tree, node)
	v.rule_id == "L011"
}

test_L011_fires_when_not_equals_false if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "x != false"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.literal_compare(tree, node)
	v.rule_id == "L011"
}

test_L011_fires_when_is_true if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "x is true"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.literal_compare(tree, node)
	v.rule_id == "L011"
}

test_L011_fires_when_is_false if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "x is false"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.literal_compare(tree, node)
	v.rule_id == "L011"
}

test_L011_fires_when_is_not_true if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "x is not true"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.literal_compare(tree, node)
	v.rule_id == "L011"
}

test_L011_fires_when_is_not_false if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "x is not false"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.literal_compare(tree, node)
	v.rule_id == "L011"
}

test_L011_fires_when_python_True if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "x == True"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.literal_compare(tree, node)
	v.rule_id == "L011"
}

test_L011_fires_when_python_False if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "x == False"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.literal_compare(tree, node)
	v.rule_id == "L011"
}

test_L011_does_not_fire_when_no_literal if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "x"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.literal_compare(tree, node)
}

test_L011_does_not_fire_when_truthiness if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "my_var | bool"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.literal_compare(tree, node)
}
