# Shared helpers for APME OPA rules.
# Package must match rule files so they can reference these definitions.

package apme.rules

short_module_name(module) := short if {
	parts := split(module, ".")
	count(parts) > 0
	short := parts[count(parts) - 1]
}

is_number(x) if {
	count(numbers.range(x, x)) >= 0
}

cmd_shell_modules[m] if {
	m := data.apme.ansible.command_shell_modules[_]
}

package_modules[m] if {
	m := data.apme.ansible.package_modules[_]
}

copy_template_modules[m] if {
	m := data.apme.ansible.copy_template_modules[_]
}

file_permission_modules[m] if {
	m := data.apme.ansible.file_permission_modules[_]
}

set_fact_modules[m] if {
	m := data.apme.ansible.set_fact_modules[_]
}

# Shell metacharacters that require ansible.builtin.shell (or make a
# command-instead-of-module substitution invalid, e.g. cat | grep).
# Inspected after Jinja {{ }} is stripped so |quote is not a pipe.
# A command that is only Jinja (nothing inspectable after the strip)
# is treated as using shell features — the rendered value is unknown.
# Use trim_space so tabs/CR match Python str.strip(), not a space-only cutset.
shell_metacharacters := ["|", "&&", "||", ";", ">", ">>", "<", "$(", "`", "*", "?", "&", "(", ")", "$", "\n"]

uses_shell_features(cmd) if {
	is_string(cmd)
	stripped := trim_space(regex.replace(cmd, `\{\{[^{}]*\}\}`, " "))
	stripped == ""
}

uses_shell_features(cmd) if {
	is_string(cmd)
	stripped := regex.replace(cmd, `\{\{[^{}]*\}\}`, " ")
	some ch in shell_metacharacters
	contains(stripped, ch)
}

has_with_loop(opts) := key if {
	some key in object.keys(opts)
	startswith(key, "with_")
	opts[key] != null
}
