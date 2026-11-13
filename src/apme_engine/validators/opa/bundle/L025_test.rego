# Integration tests for L025: Task/play name should start with uppercase

package apme.rules_test

import data.apme.rules

test_L025_fires_when_task_name_lowercase if {
	tree := {"nodes": [{"type": "taskcall", "name": "install package", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.name_casing(tree, node)
	v.rule_id == "L025"
}

test_L025_does_not_fire_when_task_name_uppercase if {
	tree := {"nodes": [{"type": "taskcall", "name": "Install package", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.name_casing(tree, node)
}

test_L025_fires_when_play_name_lowercase if {
	tree := {"nodes": [{"type": "playcall", "name": "my play", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.name_casing(tree, node)
	v.rule_id == "L025"
}

# Pipe-prefix convention: "filename | Description"
# The filename portion is not checked — only the description after " | ".

test_L025_no_fire_pipe_prefix_uppercase_description if {
	tree := {"nodes": [{"type": "taskcall", "name": "handle_error | Show error and stop", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.name_casing(tree, node)
}

test_L025_fires_pipe_prefix_lowercase_description if {
	tree := {"nodes": [{"type": "taskcall", "name": "handle_error | show error and stop", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.name_casing(tree, node)
	v.rule_id == "L025"
}

test_L025_no_fire_pipe_prefix_multiple_pipes if {
	tree := {"nodes": [{"type": "taskcall", "name": "role | sub | Do the thing", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.name_casing(tree, node)
}

test_L025_no_fire_play_pipe_prefix_uppercase if {
	tree := {"nodes": [{"type": "playcall", "name": "setup | Configure hosts", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	not rules.name_casing(tree, node)
}

test_L025_fires_trailing_pipe_by_falling_back_to_full_name if {
	tree := {"nodes": [{"type": "taskcall", "name": "handle_error | ", "line": [1], "key": "k", "file": "f.yml"}]}
	node := tree.nodes[0]
	v := rules.name_casing(tree, node)
	v.rule_id == "L025"
}

# Block wrappers carry task labels too.
test_L025_fires_when_block_name_lowercase if {
	tree := {"nodes": [{"type": "blockcall", "name": "configure service", "line": [1], "key": "k", "file": "f.yml"}]}
	v := rules.name_casing(tree, tree.nodes[0])
	v.rule_id == "L025"
}

test_L025_ignores_metadata_node_name if {
	tree := {"nodes": [{"type": "collection", "name": "my_collection", "line": [1], "key": "k", "file": "galaxy.yml"}]}
	not rules.name_casing(tree, tree.nodes[0])
}
