# Tests for M018: paramiko_ssh connection plugin

package apme.rules_test

import data.apme.rules

test_M018_fires_on_play_paramiko if {
	tree := {"nodes": [{"type": "playcall", "options": {"connection": "paramiko_ssh"}, "line": [1], "key": "k", "file": "f.yml", "name": "test"}]}
	node := tree.nodes[0]
	v := rules.paramiko_ssh_connection(tree, node)
	v.rule_id == "M018"
}

test_M018_fires_on_task_vars_paramiko if {
	tree := {"nodes": [{"type": "taskcall", "options": {"vars": {"ansible_connection": "paramiko_ssh"}}, "line": [1], "key": "k", "file": "f.yml", "module": "debug"}]}
	node := tree.nodes[0]
	v := rules.paramiko_ssh_connection(tree, node)
	v.rule_id == "M018"
}

test_M018_no_fire_on_ssh if {
	tree := {"nodes": [{"type": "playcall", "options": {"connection": "ssh"}, "line": [1], "key": "k", "file": "f.yml", "name": "test"}]}
	node := tree.nodes[0]
	not rules.paramiko_ssh_connection(tree, node)
}

test_M018_no_fire_on_local if {
	tree := {"nodes": [{"type": "playcall", "options": {"connection": "local"}, "line": [1], "key": "k", "file": "f.yml", "name": "test"}]}
	node := tree.nodes[0]
	not rules.paramiko_ssh_connection(tree, node)
}
