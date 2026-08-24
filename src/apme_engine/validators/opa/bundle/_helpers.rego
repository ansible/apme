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
# [ and ] are glob character classes (cat /tmp/[ab].txt).
shell_metacharacters := ["|", "&&", "||", ";", ">", ">>", "<", "$(", "`", "*", "?", "&", "(", ")", "[", "]", "$", "\n"]

# Non-greedy {{ ... }} so dict literals like {{ {'k': 'v'} }} are stripped.
jinja_stripped(cmd) := regex.replace(cmd, `\{\{.*?\}\}`, " ") if {
	is_string(cmd)
}

uses_shell_features(cmd) if {
	is_string(cmd)
	stripped := jinja_stripped(cmd)
	contains(stripped, "{{")
}

uses_shell_features(cmd) if {
	is_string(cmd)
	stripped := jinja_stripped(cmd)
	contains(stripped, "}}")
}

uses_shell_features(cmd) if {
	is_string(cmd)
	stripped := trim_space(jinja_stripped(cmd))
	stripped == ""
}

uses_shell_features(cmd) if {
	is_string(cmd)
	stripped := jinja_stripped(cmd)
	some ch in shell_metacharacters
	contains(stripped, ch)
}

# Prefer cmd, then free-form _raw_params, then joined argv — same sources
# as the L007 Python transform.
has_cmd_text(mo) if {
	cmd := object.get(mo, "cmd", "")
	is_string(cmd)
	trim_space(cmd) != ""
}

has_raw_text(mo) if {
	raw := object.get(mo, "_raw_params", "")
	is_string(raw)
	trim_space(raw) != ""
}

inspectable_command(mo) := cmd if {
	has_cmd_text(mo)
	cmd := object.get(mo, "cmd", "")
}

inspectable_command(mo) := raw if {
	not has_cmd_text(mo)
	has_raw_text(mo)
	raw := object.get(mo, "_raw_params", "")
}

inspectable_command(mo) := joined if {
	not has_cmd_text(mo)
	not has_raw_text(mo)
	argv := object.get(mo, "argv", [])
	is_array(argv)
	count(argv) > 0
	joined := concat(" ", [sprintf("%v", [x]) | x := argv[_]])
}

# Collapse all whitespace so first-token lookup matches trim_space emptiness
# (tabs/CR, not a space-only cutset).
normalized_cmd(cmd) := trim_space(regex.replace(cmd, `\s+`, " ")) if {
	is_string(cmd)
}

has_with_loop(opts) := key if {
	some key in object.keys(opts)
	startswith(key, "with_")
	opts[key] != null
}
