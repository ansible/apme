# Integration tests for L004: Deprecated module (requires data.apme.ansible.deprecated_modules)

package apme.rules_test

import data.apme.rules

test_L004_fires_for_deprecated_module if {
	tree := {"nodes": [{"type": "taskcall", "module": "docker", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.deprecated_module(tree, node)
	v.rule_id == "L004"
	v.severity == "high"
}

test_L004_does_not_fire_for_non_deprecated if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.copy", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.deprecated_module(tree, node)
}
