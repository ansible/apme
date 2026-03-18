# Integration tests for L017: Avoid relative path in src (uses copy_template_modules)

package apme.rules_test

import data.apme.rules

test_L017_fires_when_src_has_parent_path if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.copy", "module_options": {"src": "../../file"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.no_relative_paths(tree, node)
	v.rule_id == "L017"
}

test_L017_fires_when_src_starts_with_dot_slash if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.copy", "module_options": {"src": "./local/file"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.no_relative_paths(tree, node)
	v.rule_id == "L017"
}

test_L017_fires_when_src_relative_not_files_nor_templates if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.copy", "module_options": {"src": "other/path/file"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.no_relative_paths(tree, node)
	v.rule_id == "L017"
}

test_L017_does_not_fire_when_src_ok_templates if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.copy", "module_options": {"src": "templates/x.j2"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.no_relative_paths(tree, node)
}

test_L017_does_not_fire_when_src_ok_files if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.copy", "module_options": {"src": "files/app.conf"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.no_relative_paths(tree, node)
}
