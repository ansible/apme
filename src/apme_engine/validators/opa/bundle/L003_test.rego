# Integration tests for L003: Play should have a name

package apme.rules_test

import data.apme.rules

test_L003_fires_when_play_has_no_name if {
	tree := {"nodes": [{"type": "playcall", "name": null, "line": [1], "key": "play[0]", "file": "site.yml"}]}
	node := tree.nodes[0]
	v := rules.play_has_no_name(tree, node)
	v.rule_id == "L003"
	v.severity == "low"
}

test_L003_does_not_fire_when_play_has_name if {
	tree := {"nodes": [{"type": "playcall", "name": "My play", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.play_has_no_name(tree, node)
}

test_L003_does_not_fire_for_taskcall if {
	tree := {"nodes": [{"type": "taskcall", "name": null, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.play_has_no_name(tree, node)
}
