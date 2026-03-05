# Integration tests for L014: Use notify/handler instead of when: result.changed

package apme.rules_test

import data.apme.rules

test_L014_fires_when_dot_changed if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "result.changed"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.no_handler(tree, node)
	v.rule_id == "L014"
}

test_L014_fires_when_is_changed if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "result is changed"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.no_handler(tree, node)
	v.rule_id == "L014"
}

test_L014_fires_when_is_not_changed if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "result is not changed"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.no_handler(tree, node)
	v.rule_id == "L014"
}

test_L014_fires_when_pipe_changed if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "result|changed"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.no_handler(tree, node)
	v.rule_id == "L014"
}

test_L014_fires_when_pipe_succeeded if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "result|succeeded"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.no_handler(tree, node)
	v.rule_id == "L014"
}

test_L014_fires_when_pipe_failed if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "result|failed"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.no_handler(tree, node)
	v.rule_id == "L014"
}

test_L014_does_not_fire_when_no_changed if {
	tree := {"nodes": [{"type": "taskcall", "options": {"when": "x"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.no_handler(tree, node)
}
