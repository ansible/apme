# Tests for M009: deprecated with_* loops

package apme.rules_test

import data.apme.rules

test_M009_fires_on_with_items if {
	tree := {"nodes": [{"type": "taskcall", "options": {"with_items": ["a", "b"]}, "line": [1], "key": "k", "file": "f.yml", "module": "debug"}]}
	node := tree.nodes[0]
	v := rules.deprecated_with_loop(tree, node)
	v.rule_id == "M009"
}

test_M009_fires_on_with_dict if {
	tree := {"nodes": [{"type": "taskcall", "options": {"with_dict": {"a": 1}}, "line": [1], "key": "k", "file": "f.yml", "module": "debug"}]}
	node := tree.nodes[0]
	v := rules.deprecated_with_loop(tree, node)
	v.rule_id == "M009"
}

test_M009_no_fire_on_loop if {
	tree := {"nodes": [{"type": "taskcall", "options": {"loop": ["a", "b"]}, "line": [1], "key": "k", "file": "f.yml", "module": "debug"}]}
	node := tree.nodes[0]
	not rules.deprecated_with_loop(tree, node)
}

test_M009_no_fire_without_loop if {
	tree := {"nodes": [{"type": "taskcall", "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "debug"}]}
	node := tree.nodes[0]
	not rules.deprecated_with_loop(tree, node)
}
