# opacustom — private OPA bundle as a Plugin sidecar

Evaluates `bundle/` with `opa eval` and emits `EXT-opacustom-001` when a
task module starts with a prefix in `bundle/data.json` (default:
`community.general.`).

This container is **not** `apme-opa`. Do not add these `.rego` files to
`src/apme_engine/validators/opa/bundle`.

Full walkthrough: [PLUGIN_SIDECARS.md](../../../docs/guides/PLUGIN_SIDECARS.md).

```bash
tox -e build
tox -e build-plugins
# then uncomment plugin-opa-custom in containers/podman/pod.yaml
```
