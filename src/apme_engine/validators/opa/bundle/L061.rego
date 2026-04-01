# L061: Use true/false for booleans, not yes/no/True/False

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := truthy_boolean(tree, node)
}

_truthy_values := {"yes", "no", "Yes", "No", "YES", "NO", "True", "False", "TRUE", "FALSE", "on", "off", "On", "Off", "ON", "OFF"}

truthy_boolean(tree, node) := v if {
	node.type == "taskcall"
	opts := object.get(node, "options", {})
	some key, val in opts
	is_string(val)
	_truthy_values[val]
	count(node.line) > 0
	v := {
		"rule_id": "L061",
		"severity": "low",
		"message": sprintf("Use true/false for boolean; found '%s' in option '%s'", [val, key]),
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}

truthy_boolean(tree, node) := v if {
	node.type == "taskcall"
	mo := object.get(node, "module_options", {})
	some key, val in mo
	is_string(val)
	_truthy_values[val]
	count(node.line) > 0
	v := {
		"rule_id": "L061",
		"severity": "low",
		"message": sprintf("Use true/false for boolean; found '%s' in module option '%s'", [val, key]),
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
