# Example plugins

Sidecar implementations of the ADR-042 `Plugin` gRPC service.

| Plugin | Rule ID | How to run | Behavior |
|--------|---------|------------|----------|
| [orgpolicy](orgpolicy/) | `EXT-orgpolicy-001` | Host Python process | Flags `community.general.*` modules (detection only) |
| [opa-custom](opa-custom/) | `EXT-opacustom-001` | **Container image** (`tox -e build-plugins`) | Same banned-prefix policy as Rego in a private OPA bundle |
| [secscan](secscan/) | `EXT-secscan-<scanner_id>` | **Container image** (`tox -e build-plugins`) | Wraps [ansible-security-scanner](https://github.com/cpeoples/ansible-security-scanner) |

How to **build the images** and **add containers** to
`containers/podman/pod.yaml`:
[PLUGIN_SIDECARS.md](../../docs/guides/PLUGIN_SIDECARS.md).
Copy-paste YAML: [pod-sidecars.yaml](pod-sidecars.yaml).

Run the host-process example and point Engine at it:

```bash
APME_PLUGIN_LISTEN=0.0.0.0:50100 python examples/plugins/orgpolicy/plugin.py
export APME_PLUGIN_ORGPOLICY_ADDRESS=127.0.0.1:50100
```

Subclass `apme_plugin_sdk.PluginBase`. Do not import `apme_engine` from plugin code.
The Engine sends `files` and `hierarchy_payload`; `scandata` is always empty.
`Transform` (when implemented) returns replacement **node YAML**, not a full file.
