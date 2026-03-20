# L005: Use only ansible.builtin or ansible.legacy
#
# The engine resolves short module names to FQCNs, so node.module may
# already be "ansible.builtin.apt" even though the YAML says "apt".
# Check original_module (the literal YAML key) to catch short names that
# need to be written as FQCN in the source file.

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := only_builtins(tree, node)
}

_is_fqcn(s) if {
	contains(s, ".")
	not contains(s, "/")
	not contains(s, " ")
	not contains(s, "#")
	not startswith(s, "taskfile")
}

# Variant with a usable resolved FQCN (transform can auto-fix)
only_builtins(tree, node) := v if {
	node.type == "taskcall"
	om := object.get(node, "original_module", node.module)
	om != ""
	not startswith(om, "ansible.builtin.")
	not startswith(om, "ansible.legacy.")
	count(node.line) > 0
	resolved := node.module
	_is_fqcn(resolved)
	v := {
		"rule_id": "L005",
		"level": "warning",
		"message": sprintf("Use FQCN: %s -> %s", [om, resolved]),
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"original_module": om,
		"resolved_fqcn": resolved,
		"scope": "task",
	}
}

# Variant without a resolvable FQCN (detection only, escalate to AI/manual)
only_builtins(tree, node) := v if {
	node.type == "taskcall"
	om := object.get(node, "original_module", node.module)
	om != ""
	not startswith(om, "ansible.builtin.")
	not startswith(om, "ansible.legacy.")
	count(node.line) > 0
	not _is_fqcn(node.module)
	not _is_fqcn(om)
	v := {
		"rule_id": "L005",
		"level": "warning",
		"message": sprintf("Use FQCN for: %s", [om]),
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"original_module": om,
		"scope": "task",
	}
}
