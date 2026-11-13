# L025: Task/play name should start with uppercase letter
#
# Names following the "filename | Description" convention are checked on
# the description portion only — the filename prefix is not required to
# be uppercase.

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := name_casing(tree, node)
}

# Extract the meaningful portion of a name.
# If the name contains " | " and the suffix is non-empty, use the suffix.
# Otherwise fall back to the whole name so malformed names like "file | "
# still evaluate deterministically instead of leaving the helper undefined.
_name_subject(name) := subject if {
	contains(name, " | ")
	parts := split(name, " | ")
	last := parts[count(parts) - 1]
	last != ""
	subject := last
}

_name_subject(name) := subject if {
	not contains(name, " | ")
	subject := name
}

_name_subject(name) := subject if {
	contains(name, " | ")
	parts := split(name, " | ")
	last := parts[count(parts) - 1]
	last == ""
	subject := name
}

_effective_first(name) := first if {
	subject := _name_subject(name)
	first := substring(subject, 0, 1)
}

name_casing(tree, node) := v if {
	node.type in {"taskcall", "blockcall"}
	node.name != ""
	first := _effective_first(node.name)
	lower(first) == first
	count(node.line) > 0
	v := {
		"rule_id": "L025",
		"severity": "low",
		"message": "Task name should start with an uppercase letter",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}

name_casing(tree, node) := v if {
	node.type == "playcall"
	node.name != ""
	first := _effective_first(node.name)
	lower(first) == first
	count(node.line) > 0
	v := {
		"rule_id": "L025",
		"severity": "low",
		"message": "Play name should start with an uppercase letter",
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "task",
	}
}
