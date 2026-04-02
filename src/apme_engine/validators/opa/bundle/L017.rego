# L017: Avoid relative path in src (uses copy_template_modules from _helpers.rego)

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := no_relative_paths(tree, node)
}

no_relative_paths(tree, node) := v if {
	node.type == "taskcall"
	copy_template_modules[node.module]
	mo := object.get(node, "module_options", {})
	src := object.get(mo, "src", "")
	is_string(src)
	trim(src, " ") != ""
	# Flag ../ parent traversal or ./ current-dir relative (role-relative files/ and templates/ are ok)
	relative_bad(src)
	count(node.line) > 0
	v := {
		"rule_id": "L017",
		"severity": "low",
		"message": "Avoid relative path in src; use role-relative paths (files/, templates/) or absolute path",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}

# relative_bad(s): src uses ../ or ./ or other relative path not under files/ or templates/
relative_bad(src) if {
	contains(src, "../")
}

relative_bad(src) if {
	startswith(src, "./")
}

relative_bad(src) if {
	not startswith(src, "/")
	not contains(src, "://")
	not startswith(src, "files/")
	not startswith(src, "templates/")
	contains(src, "/")
}
