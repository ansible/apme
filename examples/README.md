# Examples

## CI/CD Integration

See [ci/](ci/) for ready-to-use GitHub Actions workflows and pre-commit
hook configurations. Copy these to your Ansible repos to integrate APME
into your pipelines.

## Example plugins

[`plugins/`](plugins/) has ADR-042 Plugin sidecars:

- [`orgpolicy/`](plugins/orgpolicy/) — host-process example (`EXT-orgpolicy-001`)
- [`opa-custom/`](plugins/opa-custom/) — **image**: private OPA bundle (not the built-in `opa` container)
- [`secscan/`](plugins/secscan/) — **image**: [ansible-security-scanner](https://github.com/cpeoples/ansible-security-scanner)

Build images and add containers to the Podman pod:
[PLUGIN_SIDECARS.md](../docs/guides/PLUGIN_SIDECARS.md) (`tox -e build-plugins`).

```bash
APME_PLUGIN_LISTEN=0.0.0.0:50100 python examples/plugins/orgpolicy/plugin.py
export APME_PLUGIN_ORGPOLICY_ADDRESS=127.0.0.1:50100
```

See [ADR-042](../.sdlc/adrs/ADR-042-third-party-plugin-services.md) and
[Rule Configuration](../docs/guides/RULE_CONFIGURATION.md#third-party-plugin-services-adr-042).

## Example Playbooks

These playbooks are **intentionally non-conformant**. They exist to
demonstrate and test APME's scanner, auto-fix, and remediation capabilities.

**Do not "fix" these files** — the lint violations, bad practices, and fake
secrets are by design. **Do not run these playbooks** with
`ansible-playbook` — several contain tasks that modify system state
(package installs, file writes, user creation).

## Files

| File | Rules demonstrated |
|------|--------------------|
| `bad_practices.yml` | L002–L015, L024 — FQCN, ignore\_errors, state=latest |
| `risky_permissions.yml` | L018–L022, L031 — file modes, become, shell pipes |
| `style_violations.yml` | L025, L041–L050 — naming, key order, free-form |
| `complex_playbook.yml` | L003, L016–L017, L023, L042 — complexity, prompts |
| `module_issues.yml` | L026, L005, L037 — non-FQCN, community use, unresolved |
| `secrets_example.yml` | SEC rules — AWS keys, GitHub PAT, private keys |
| `roles/broken_role/` | L027–L039 — missing metadata, undefined vars |
| `minimal_playbook.yml` | Minimal baseline with a few issues |

## Usage

```bash
# Check all examples (binary: apme)
apme check examples/

# Remediate what can be fixed (on a copy!)
cp -r examples/ /tmp/examples-copy
# Dry-run with diffs (no writes): apme check --diff /tmp/examples-copy/
apme remediate /tmp/examples-copy/
```
