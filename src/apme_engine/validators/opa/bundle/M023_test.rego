# Tests for M023: follow_redirects string boolean

package apme.rules_test

import data.apme.rules

test_M023_fires_on_yes_string if {
	tree := {"nodes": [{"type": "taskcall", "module_options": {"follow_redirects": "yes"}, "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "uri"}]}
	node := tree.nodes[0]
	v := rules.follow_redirects_string(tree, node)
	v.rule_id == "M023"
}

test_M023_fires_on_no_string if {
	tree := {"nodes": [{"type": "taskcall", "module_options": {"follow_redirects": "no"}, "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "uri"}]}
	node := tree.nodes[0]
	v := rules.follow_redirects_string(tree, node)
	v.rule_id == "M023"
}

test_M023_no_fire_on_boolean if {
	tree := {"nodes": [{"type": "taskcall", "module_options": {"follow_redirects": true}, "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "uri"}]}
	node := tree.nodes[0]
	not rules.follow_redirects_string(tree, node)
}
