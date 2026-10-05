# Plugin sidecar images (Podman pod)

This guide shows how to **build a plugin image** and **add it as an extra
container** in [`containers/podman/pod.yaml`](../../containers/podman/pod.yaml).

Use this path for organization-specific checks and third-party scanners.
Do **not** add a new built-in validator (that is a product change: proto,
daemon, Helm, closed rule bundles). Plugins are the ADR-042 sidecar
contract: `Validate` + optional `Transform` + `Describe` + `Health`.

Two copy-paste examples ship in-tree:

| Example | Image | Port | Engine env | What it wraps |
|---------|-------|------|------------|----------------|
| [OPA custom bundle](../../examples/plugins/opa-custom/) | `apme-plugin-opa-custom:latest` | 50100 | `APME_PLUGIN_OPACUSTOM_ADDRESS` | Your Rego, evaluated with `opa eval` in a **separate** container |
| [ansible-security-scanner](../../examples/plugins/secscan/) | `apme-plugin-secscan:latest` | 50101 | `APME_PLUGIN_SECSCAN_ADDRESS` | [cpeoples/ansible-security-scanner](https://github.com/cpeoples/ansible-security-scanner) |

Worked YAML to paste into the pod is also in
[`examples/plugins/pod-sidecars.yaml`](../../examples/plugins/pod-sidecars.yaml)
(commented in `pod.yaml` so `tox -e up` still works without these images).

## Built-in OPA vs an OPA plugin

The pod already has a built-in **`opa`** container (`apme-opa:latest`, port
50054, `OPA_GRPC_ADDRESS`). That image copies
`src/apme_engine/validators/opa/bundle` **at build time**. The bundle is
**closed** (ADR-042): do not volume-mount extra `.rego` files into it, do
not point `OpaValidator(bundle_path=...)` at org policy in production, and
do not fork `containers/opa/Dockerfile` to bake custom rules.

Custom Rego belongs in a **Plugin** sidecar that:

1. Ships **its own** bundle directory.
2. Runs the official `opa` binary (`opa eval -I -d /bundle …`).
3. Implements `Plugin` gRPC (not `Validator`) via `apme_plugin_sdk`.
4. Emits rule IDs under `EXT-<name>-…` (for example `EXT-opacustom-001`).

Engine discovery is env-only (ADR-005):

```bash
APME_PLUGIN_<NAME>_ADDRESS=127.0.0.1:<port>
```

`<NAME>` is uppercase alphanumeric. The Engine lowercases it (`OPACUSTOM` →
plugin name `opacustom`, prefix `EXT-opacustom-`). Ports **50100–50199**.

Plugins are optional for Engine `Health`. A configured plugin whose
`Validate` RPC fails still emits `EXT-<name>-unavailable` so `apme check`
is not a silent pass.

## Checklist (any plugin)

1. **Implement** `PluginBase` (`validate`, optional `transform`).
2. **Dockerfile** `FROM localhost/apme-base:latest` so `apme_plugin_sdk` and
   generated `apme.v1` stubs are already installed. Copy only plugin code
   (and, for OPA, the `opa` binary + bundle).
3. **Listen** on `APME_PLUGIN_LISTEN` (default `0.0.0.0:50100`).
4. **Build** from the **repo root** (build context must include `examples/`
   and the base image):
   ```bash
   tox -e build                 # apme-base + product images
   tox -e build-plugins         # optional sidecar images in this guide
   ```
5. **Pod**: extra container + `APME_PLUGIN_<NAME>_ADDRESS` on **engine**.
6. **Restart** the pod (`tox -e down` then `tox -e up` after uncommenting,
   or recreate the pod after editing `pod.yaml`).
7. **Verify**: `tox -e cli -- health-check`, then
   `tox -e cli -- check /workspace` on a project that should fire the
   rule. Plugin rows (`plugin:<name>`) are listed as optional. A non-ok
   plugin does **not** fail Engine aggregate health or the CLI exit code.
   If `Describe` fails, Engine still calls `Validate` (stub identity;
   failed Describes are not cached). Helm does **not** inject
   `APME_PLUGIN_*` — cluster attach is the operator CR
   ([apme-operator#39](https://github.com/ansible/apme-operator/issues/39)).

Cluster attach (OpenShift / Kubernetes) is the APME Operator
`Apme.spec.plugins[]` — see [apme-operator#39](https://github.com/ansible/apme-operator/issues/39).
This guide is the **in-repo Podman pod** equivalent.

## 1. Custom OPA plugin

Files: [`examples/plugins/opa-custom/`](../../examples/plugins/opa-custom/).

The sample Rego flags `community.general.*` task modules as
`EXT-opacustom-001`. Banned prefixes live in `bundle/data.json` so you can
edit policy data without touching Python.

### Build the image

```bash
# Product base image (needed once; also part of tox -e build)
tox -e build

# Plugin sidecar only
tox -e build-plugins
# equivalent:
# podman build -t apme-plugin-opa-custom:latest \
#   -f examples/plugins/opa-custom/Dockerfile .
```

The Dockerfile copies the OPA binary from
`docker.io/openpolicyagent/opa:1.17.1` (same pin as `containers/opa`) and
bakes `examples/plugins/opa-custom/bundle` into `/bundle`. There is **no**
runtime volume for Rego.

### Add the container to `pod.yaml`

On the **engine** container, add:

```yaml
        - name: APME_PLUGIN_OPACUSTOM_ADDRESS
          value: "127.0.0.1:50100"
```

Alongside the other containers (after `gitleaks` is a good place):

```yaml
    - name: plugin-opa-custom
      image: apme-plugin-opa-custom:latest
      env:
        - name: APME_PLUGIN_LISTEN
          value: "0.0.0.0:50100"
        - name: APME_OPA_PLUGIN_BUNDLE
          value: "/bundle"
        - name: APME_OPA_PLUGIN_ENTRYPOINT
          value: "data.apme.plugin.violations"
      ports:
        - containerPort: 50100
```

Do **not** set `OPA_GRPC_ADDRESS` to this sidecar. That env var is the
built-in Validator. Plugins use `APME_PLUGIN_*_ADDRESS` only.

### Host process (no image)

If you only want to try the gRPC server on the laptop:

```bash
# needs `opa` on PATH
export APME_OPA_PLUGIN_BUNDLE="$PWD/examples/plugins/opa-custom/bundle"
APME_PLUGIN_LISTEN=0.0.0.0:50100 python examples/plugins/opa-custom/plugin.py
export APME_PLUGIN_OPACUSTOM_ADDRESS=127.0.0.1:50100
```

### Authoring more Rego

- Package: `apme.plugin` (not `apme.rules` — that package is the closed
  built-in bundle).
- Input: Engine `hierarchy_payload` JSON. Task modules are
  `input.hierarchy[_].nodes[_]` with `type` `taskcall` / similar and a
  `module` string (same shape the built-in OPA validator sees).
- Each violation **must** use an `EXT-opacustom-` rule ID (or a bare
  suffix such as `001`; the SDK prefixes it).
- Optional `ai_guidance` string is stored on violation metadata for ADR-042 Phase 4.

## 2. ansible-security-scanner plugin

Files: [`examples/plugins/secscan/`](../../examples/plugins/secscan/).

[Ansible Security Scanner](https://github.com/cpeoples/ansible-security-scanner)
is a standalone SAST CLI/library (Apache-2.0, Chris Peoples). It is **not**
an APME built-in validator. The example container installs the scanner and
wraps `AnsibleSecurityScanner.scan_directory()` behind `Plugin.Validate`.

Findings become `EXT-secscan-<scanner_rule_id>` (for example
`EXT-secscan-hardcoded_password`). Detection only: the scanner’s `--fix`
unified diffs are **whole-file** patches; ADR-042 `Transform` is
**node YAML** only, so this sidecar does not implement `Transform`.
Remaining `EXT-secscan-*` findings are **manual review** until ADR-042
Phase 4 (per-plugin AI batching). `ai_guidance` stores the scanner
`recommendation` for that future path.

### Build the image

```bash
tox -e build
tox -e build-plugins
# equivalent:
# podman build -t apme-plugin-secscan:latest \
#   -f examples/plugins/secscan/Dockerfile .
```

The Dockerfile `pip`-installs a pinned scanner release into the APME venv.
Override the pin:

```bash
podman build -t apme-plugin-secscan:latest \
  --build-arg SECSCAN_VERSION=0.1.39 \
  -f examples/plugins/secscan/Dockerfile .
```

### Add the container to `pod.yaml`

On **engine**:

```yaml
        - name: APME_PLUGIN_SECSCAN_ADDRESS
          value: "127.0.0.1:50101"
```

Sidecar (different port from the OPA plugin):

```yaml
    - name: plugin-secscan
      image: apme-plugin-secscan:latest
      env:
        - name: APME_PLUGIN_LISTEN
          value: "0.0.0.0:50101"
      ports:
        - containerPort: 50101
```

The wrapper writes `ValidateRequest.files` into an ephemeral directory and
runs the scanner there. Plugins do not mount `/sessions` and must not
write the user tree.

### Host process (no image)

```bash
pip install "ansible-security-scanner==0.1.39"   # or current release
APME_PLUGIN_LISTEN=0.0.0.0:50101 python examples/plugins/secscan/plugin.py
export APME_PLUGIN_SECSCAN_ADDRESS=127.0.0.1:50101
```

## Enabling both sidecars

Uncomment the matching blocks in `containers/podman/pod.yaml` (engine env
+ two containers), or copy from `examples/plugins/pod-sidecars.yaml`.

```bash
tox -e build
tox -e build-plugins
tox -e down
tox -e up
tox -e cli -- health-check
```

`tox -e up` does **not** build plugin images (keeps the default pod
offline-friendly). Build them with `tox -e build-plugins` first or the
pod will fail to start those containers.

`tox -e cli -- health-check` prints `plugin:opacustom` / `plugin:secscan`
when those env vars are set. Those rows can be non-ok without failing
the command (plugins are optional). The published Helm chart does not
set `APME_PLUGIN_*`; use the operator `spec.plugins[]` CR on Kubernetes.

## What not to do

| Wrong | Right |
|-------|--------|
| Add `.rego` under `src/apme_engine/validators/opa/bundle/` for org policy | Plugin image with its own `/bundle` |
| New row in `_DEFAULT_PORTS` / `VALIDATOR_ENV_VARS` for a 3rd-party tool | `APME_PLUGIN_<NAME>_ADDRESS` |
| Point `OPA_GRPC_ADDRESS` at a Plugin | Plugin env vars only |
| Implement `Validator` in the sidecar | Implement `Plugin` (`plugin.proto`) |
| Rule IDs `P001` / `L999` from a plugin | `EXT-<name>-…` |
| Mount playbooks into the plugin and rewrite files | Return node YAML from `Transform`; Engine applies it |

Gitleaks in `pod.yaml` remains the pattern for **built-in optional
Validators** (`GITLEAKS_GRPC_ADDRESS`, `containers/gitleaks/Dockerfile`).
Copy that only when adding a first-party APME validator, not a plugin.
