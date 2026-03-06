# Tests for M011: network collection compat

package apme.rules_test

import data.apme.rules

test_M011_fires_on_ios_module if {
	tree := {"nodes": [{"type": "taskcall", "module": "cisco.ios.ios_command", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.network_compat(tree, node)
	v.rule_id == "M011"
}

test_M011_fires_on_nxos_module if {
	tree := {"nodes": [{"type": "taskcall", "module": "cisco.nxos.nxos_config", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.network_compat(tree, node)
	v.rule_id == "M011"
}

test_M011_fires_on_netcommon if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.netcommon.cli_command", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.network_compat(tree, node)
	v.rule_id == "M011"
}

test_M011_no_fire_on_builtin if {
	tree := {"nodes": [{"type": "taskcall", "module": "ansible.builtin.debug", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.network_compat(tree, node)
}
