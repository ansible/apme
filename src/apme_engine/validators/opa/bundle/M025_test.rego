# Tests for M025: Third-party strategy plugin

package apme.rules_test

import data.apme.rules

test_M025_fires_on_mitogen if {
	tree := {"nodes": [{"type": "playcall", "options": {"strategy": "mitogen_linear"}, "line": [1], "key": "k", "file": "f.yml", "name": "test"}]}
	node := tree.nodes[0]
	v := rules.third_party_strategy(tree, node)
	v.rule_id == "M025"
}

test_M025_no_fire_on_linear if {
	tree := {"nodes": [{"type": "playcall", "options": {"strategy": "linear"}, "line": [1], "key": "k", "file": "f.yml", "name": "test"}]}
	node := tree.nodes[0]
	not rules.third_party_strategy(tree, node)
}

test_M025_no_fire_on_free if {
	tree := {"nodes": [{"type": "playcall", "options": {"strategy": "free"}, "line": [1], "key": "k", "file": "f.yml", "name": "test"}]}
	node := tree.nodes[0]
	not rules.third_party_strategy(tree, node)
}

test_M025_no_fire_without_strategy if {
	tree := {"nodes": [{"type": "playcall", "options": {}, "line": [1], "key": "k", "file": "f.yml", "name": "test"}]}
	node := tree.nodes[0]
	not rules.third_party_strategy(tree, node)
}
