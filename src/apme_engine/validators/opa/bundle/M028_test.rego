# Tests for M028: first_found auto-splitting paths

package apme.rules_test

import data.apme.rules

test_M028_fires_on_comma_terms if {
	tree := {"nodes": [{"type": "taskcall", "module_options": {"terms": "a.yml,b.yml"}, "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "first_found"}]}
	node := tree.nodes[0]
	v := rules.first_found_auto_split(tree, node)
	v.rule_id == "M028"
}

test_M028_fires_on_colon_terms if {
	tree := {"nodes": [{"type": "taskcall", "module_options": {"terms": "a.yml:b.yml"}, "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "first_found"}]}
	node := tree.nodes[0]
	v := rules.first_found_auto_split(tree, node)
	v.rule_id == "M028"
}

test_M028_no_fire_on_list if {
	tree := {"nodes": [{"type": "taskcall", "module_options": {"terms": ["a.yml", "b.yml"]}, "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "first_found"}]}
	node := tree.nodes[0]
	not rules.first_found_auto_split(tree, node)
}

test_M028_no_fire_on_single_term if {
	tree := {"nodes": [{"type": "taskcall", "module_options": {"terms": "a.yml"}, "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "first_found"}]}
	node := tree.nodes[0]
	not rules.first_found_auto_split(tree, node)
}

test_M028_no_fire_on_url if {
	tree := {"nodes": [{"type": "taskcall", "module_options": {"terms": "https://example.com/vars.yml"}, "options": {}, "line": [1], "key": "k", "file": "f.yml", "module": "first_found"}]}
	node := tree.nodes[0]
	not rules.first_found_auto_split(tree, node)
}
