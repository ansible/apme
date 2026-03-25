# Tests for M024: include_vars ignore_files as string

package apme.rules_test

import data.apme.rules

test_M024_fires_on_string_ignore_files if {
	tree := {"nodes": [{"type": "taskcall", "module_options": {"ignore_files": "*.bak", "dir": "vars/"}, "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "ansible.builtin.include_vars"}]}
	node := tree.nodes[0]
	v := rules.include_vars_ignore_files_string(tree, node)
	v.rule_id == "M024"
}

test_M024_no_fire_on_list if {
	tree := {"nodes": [{"type": "taskcall", "module_options": {"ignore_files": ["*.bak"], "dir": "vars/"}, "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "ansible.builtin.include_vars"}]}
	node := tree.nodes[0]
	not rules.include_vars_ignore_files_string(tree, node)
}

test_M024_no_fire_on_other_module if {
	tree := {"nodes": [{"type": "taskcall", "module_options": {"ignore_files": "*.bak"}, "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "ansible.builtin.copy"}]}
	node := tree.nodes[0]
	not rules.include_vars_ignore_files_string(tree, node)
}
