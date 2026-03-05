# Integration tests for L022: Shell with pipe should set pipefail

package apme.rules_test

import data.apme.rules

test_L022_fires_when_shell_pipe_no_pipefail if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.shell", "module_options": {"cmd": "cat f | grep x"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.risky_shell_pipe(tree, node)
	v.rule_id == "L022"
}

test_L022_does_not_fire_when_pipefail_in_cmd if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.shell", "module_options": {"cmd": "set -o pipefail; cat f | grep x"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.risky_shell_pipe(tree, node)
}

test_L022_does_not_fire_when_no_pipe if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.shell", "module_options": {"cmd": "echo hi"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.risky_shell_pipe(tree, node)
}

test_L022_fires_for_legacy_shell if {
	tree := {"nodes": [{"type": "taskcall", "module": "shell", "module_options": {"cmd": "cat f | grep x"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.risky_shell_pipe(tree, node)
	v.rule_id == "L022"
}

test_L022_does_not_fire_for_command_module if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.command", "module_options": {"cmd": "echo hi"}, "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.risky_shell_pipe(tree, node)
}
