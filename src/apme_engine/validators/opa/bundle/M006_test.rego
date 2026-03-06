# Tests for M006: become timeout unreachable

package apme.rules_test

import data.apme.rules

test_M006_fires_become_ignore_errors if {
	tree := {"nodes": [{"type": "taskcall", "options": {"become": true, "ignore_errors": true}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.become_timeout_risk(tree, node)
	v.rule_id == "M006"
}

test_M006_no_fire_with_ignore_unreachable if {
	tree := {"nodes": [{"type": "taskcall", "options": {"become": true, "ignore_errors": true, "ignore_unreachable": true}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.become_timeout_risk(tree, node)
}

test_M006_no_fire_without_become if {
	tree := {"nodes": [{"type": "taskcall", "options": {"ignore_errors": true}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.become_timeout_risk(tree, node)
}

test_M006_no_fire_without_ignore_errors if {
	tree := {"nodes": [{"type": "taskcall", "options": {"become": true}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.become_timeout_risk(tree, node)
}
