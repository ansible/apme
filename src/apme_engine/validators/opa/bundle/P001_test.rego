# Tests for P001: Banned collection - community.general

package apme.rules_test

import data.apme.rules

test_P001_fires_for_community_general if {
	tree := {"nodes": [{"type": "taskcall", "module": "community.general.sysctl", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.banned_collection(tree, node)
	v.rule_id == "P001"
	v.severity == "high"
	contains(v.message, "community.general.sysctl")
}

test_P001_fires_for_community_general_fqcn if {
	tree := {"nodes": [{"type": "taskcall", "module": "community.general.ini_file", "line": [42], "key": "k", "file": "playbook.yml"}]}
	node := tree.nodes[0]
	v := rules.banned_collection(tree, node)
	v.rule_id == "P001"
	v.line == 42
}

test_P001_does_not_fire_for_builtin if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.copy", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.banned_collection(tree, node)
}

test_P001_does_not_fire_for_other_collection if {
	tree := {"nodes": [{"type": "taskcall", "module": "community.docker.docker_container", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.banned_collection(tree, node)
}

test_P001_does_not_fire_for_non_task if {
	tree := {"nodes": [{"type": "block", "module": "", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.banned_collection(tree, node)
}
