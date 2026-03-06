# Tests for M008: bare include removed

package apme.rules_test

import data.apme.rules

test_M008_fires_on_bare_include if {
	tree := {"nodes": [{"type": "taskcall", "module": "include", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.bare_include(tree, node)
	v.rule_id == "M008"
}

test_M008_no_fire_on_include_tasks if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.include_tasks", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.bare_include(tree, node)
}

test_M008_no_fire_on_import_tasks if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.import_tasks", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.bare_include(tree, node)
}
