# secscan — Ansible Security Scanner as a Plugin sidecar

Wraps [ansible-security-scanner](https://github.com/cpeoples/ansible-security-scanner)
(Apache-2.0, Chris Peoples) behind the ADR-042 `Plugin` gRPC service.

Findings are reported as `EXT-secscan-<scanner_rule_id>`. There is no
`Transform` (scanner autofix emits unified diffs; Engine Transform is
node YAML).

Full walkthrough: [PLUGIN_SIDECARS.md](../../../docs/guides/PLUGIN_SIDECARS.md).

```bash
tox -e build
tox -e build-plugins
# then uncomment plugin-secscan in containers/podman/pod.yaml
```
