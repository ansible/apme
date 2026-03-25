# Tests for M017: action as mapping

package apme.rules_test

import data.apme.rules

test_M017_fires_on_action_dict if {
	tree := {"nodes": [{"type": "taskcall", "options": {"action": {"module": "copy", "src": "a"}}, "line": [1], "key": "k", "file": "f.yml", "module": "copy"}]}
	node := tree.nodes[0]
	v := rules.action_as_mapping(tree, node)
	v.rule_id == "M017"
}

test_M017_no_fire_on_action_string if {
	tree := {"nodes": [{"type": "taskcall", "options": {"action": "copy src=a"}, "line": [1], "key": "k", "file": "f.yml", "module": "copy"}]}
	node := tree.nodes[0]
	not rules.action_as_mapping(tree, node)
}

test_M017_no_fire_without_action if {
	tree := {"nodes": [{"type": "taskcall", "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "debug"}]}
	node := tree.nodes[0]
	not rules.action_as_mapping(tree, node)
}
