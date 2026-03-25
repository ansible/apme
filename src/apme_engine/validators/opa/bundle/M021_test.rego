# Tests for M021: Empty args keyword

package apme.rules_test

import data.apme.rules

test_M021_fires_on_null_args if {
	tree := {"nodes": [{"type": "taskcall", "options": {"args": null}, "line": [1], "key": "k", "file": "f.yml", "module": "command"}]}
	node := tree.nodes[0]
	v := rules.empty_args_keyword(tree, node)
	v.rule_id == "M021"
}

test_M021_fires_on_empty_dict_args if {
	tree := {"nodes": [{"type": "taskcall", "options": {"args": {}}, "line": [1], "key": "k", "file": "f.yml", "module": "command"}]}
	node := tree.nodes[0]
	v := rules.empty_args_keyword(tree, node)
	v.rule_id == "M021"
}

test_M021_no_fire_on_real_args if {
	tree := {"nodes": [{"type": "taskcall", "options": {"args": {"chdir": "/tmp"}}, "line": [1], "key": "k", "file": "f.yml", "module": "command"}]}
	node := tree.nodes[0]
	not rules.empty_args_keyword(tree, node)
}

test_M021_no_fire_without_args if {
	tree := {"nodes": [{"type": "taskcall", "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "debug"}]}
	node := tree.nodes[0]
	not rules.empty_args_keyword(tree, node)
}
