# Integration tests for L006: Command used in place of preferred module

package apme.rules_test

import data.apme.rules

# L006 needs data.apme.ansible.command_to_module and cmd_shell_modules; tested with bundle
test_L006_does_not_fire_for_plain_task if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.copy", "module_options": {}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.command_instead_of_module(tree, node)
}

test_L006_fires_for_simple_cat if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.command", "module_options": {"cmd": "cat /etc/hostname"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.command_instead_of_module(tree, node)
	v.rule_id == "L006"
}

test_L006_does_not_fire_when_cmd_has_pipe if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.command", "module_options": {"cmd": "cat /proc/meminfo | grep MemTotal"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.command_instead_of_module(tree, node)
}

test_L006_does_not_fire_when_cmd_has_and if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.shell", "module_options": {"cmd": "cat /tmp/a && cat /tmp/b"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.command_instead_of_module(tree, node)
}

test_L006_fires_when_pipe_is_jinja_filter if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.command", "module_options": {"cmd": "cat {{ myfile|quote }}"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.command_instead_of_module(tree, node)
	v.rule_id == "L006"
}

test_L006_does_not_fire_when_cmd_has_background if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.command", "module_options": {"cmd": "sleep 10 &"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.command_instead_of_module(tree, node)
}

test_L006_does_not_fire_when_command_is_only_jinja if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.command", "module_options": {"cmd": "{{ command }}"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.command_instead_of_module(tree, node)
}
