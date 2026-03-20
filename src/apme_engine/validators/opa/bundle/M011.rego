# M011: Network collection modules may be incompatible with 2.19+ data tagging

package apme.rules

import future.keywords.if
import future.keywords.in

violations contains v if {
	some tree in input.hierarchy
	some node in tree.nodes
	v := network_compat(tree, node)
}

_network_prefixes := [
	"cisco.ios.", "cisco.nxos.", "cisco.iosxr.",
	"arista.eos.", "junipernetworks.junos.",
	"ansible.netcommon.",
]

network_compat(tree, node) := v if {
	node.type == "taskcall"
	node.module != ""
	some prefix in _network_prefixes
	startswith(node.module, prefix)
	count(node.line) > 0
	v := {
		"rule_id": "M011",
		"level": "info",
		"message": sprintf("Network module %s may require collection upgrade for ansible-core 2.19+ compatibility", [node.module]),
		"file": node.file,
		"line": node.line[0],
		"path": node.key,
		"scope": "collection",
	}
}
